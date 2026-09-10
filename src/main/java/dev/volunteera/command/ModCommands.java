package dev.volunteera.command;

import java.util.Collection;
import java.util.Locale;

import com.mojang.brigadier.CommandDispatcher;
import com.mojang.brigadier.arguments.IntegerArgumentType;
import com.mojang.brigadier.arguments.StringArgumentType;
import com.mojang.brigadier.builder.LiteralArgumentBuilder;
import com.mojang.brigadier.context.CommandContext;
import com.mojang.brigadier.exceptions.CommandSyntaxException;
import com.mojang.brigadier.suggestion.SuggestionProvider;

import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.Identifier;
import net.minecraft.server.level.ServerPlayer;

import dev.volunteera.VolunteeraMod;
import dev.volunteera.api.Abilities;
import dev.volunteera.api.Ability;
import dev.volunteera.api.Origin;
import dev.volunteera.api.Origins;
import dev.volunteera.api.Trial;
import dev.volunteera.api.Trials;
import dev.volunteera.config.ModConfig;
import dev.volunteera.net.StateSync;
import dev.volunteera.progress.ProgressData;
import dev.volunteera.progress.ProgressManager;
import dev.volunteera.runtime.PlayerRuntime;
import dev.volunteera.runtime.RuntimeManager;
import dev.volunteera.runtime.FlightController;
import dev.volunteera.server.OriginAttributes;
import dev.volunteera.server.EventHooks;

/**
 * Operator-only development and administration commands (/volunteera, level 2).
 * Clearly separated from gameplay: ordinary players can never touch these.
 */
public final class ModCommands {
	private ModCommands() {
	}

	private static final SuggestionProvider<CommandSourceStack> ORIGIN_SUGGESTIONS = (context, builder) -> {
		builder.suggest("none");
		for (Origin origin : Origins.all()) {
			builder.suggest(origin.id().getPath());
		}
		return builder.buildFuture();
	};

	private static final SuggestionProvider<CommandSourceStack> ABILITY_SUGGESTIONS = (context, builder) -> {
		for (Ability ability : Abilities.all()) {
			builder.suggest(ability.id().getPath());
		}
		return builder.buildFuture();
	};

	private static final SuggestionProvider<CommandSourceStack> TRIAL_SUGGESTIONS = (context, builder) -> {
		for (Trial trial : Trials.all()) {
			builder.suggest(trial.id().getPath());
		}
		return builder.buildFuture();
	};

	public static void register(CommandDispatcher<CommandSourceStack> dispatcher) {
		LiteralArgumentBuilder<CommandSourceStack> root = Commands.literal("volunteera")
				.requires(Commands.hasPermission(Commands.LEVEL_GAMEMASTERS));

		root.then(Commands.literal("info").executes(ModCommands::info));

		root.then(Commands.literal("origin")
				.then(Commands.argument("origin", StringArgumentType.word())
						.suggests(ORIGIN_SUGGESTIONS)
						.executes(ModCommands::setOrigin)));

		root.then(Commands.literal("ability")
				.then(Commands.literal("unlock")
						.then(Commands.argument("ability", StringArgumentType.word())
								.suggests(ABILITY_SUGGESTIONS)
								.executes(ModCommands::unlockAbility))
						.then(Commands.literal("all").executes(ModCommands::unlockAll)))
				.then(Commands.literal("level")
						.then(Commands.argument("ability", StringArgumentType.word())
								.suggests(ABILITY_SUGGESTIONS)
								.then(Commands.argument("level", IntegerArgumentType.integer(0, 3))
										.executes(ModCommands::setLevel)))));

		root.then(Commands.literal("trial")
				.then(Commands.literal("progress")
						.then(Commands.argument("trial", StringArgumentType.word())
								.suggests(TRIAL_SUGGESTIONS)
								.then(Commands.argument("amount", IntegerArgumentType.integer(0))
										.executes(ModCommands::trialProgress))))
				.then(Commands.literal("complete")
						.then(Commands.argument("trial", StringArgumentType.word())
								.suggests(TRIAL_SUGGESTIONS)
								.executes(ModCommands::trialComplete))));

		root.then(Commands.literal("resources").executes(ModCommands::refillResources));
		root.then(Commands.literal("cooldowns").executes(ModCommands::resetCooldowns));
		root.then(Commands.literal("reset").executes(ModCommands::resetProgress));
		root.then(Commands.literal("testmode")
				.then(Commands.literal("on").executes(ctx -> testmode(ctx, true)))
				.then(Commands.literal("off").executes(ctx -> testmode(ctx, false))));

		root.then(Commands.literal("config")
				.then(Commands.literal("reload").executes(ctx -> {
					ModConfig.reload();
					ctx.getSource().sendSuccess(() -> Component.translatable("command.volunteera.config.reloaded"), false);
					return 1;
				})));

		dispatcher.register(root);
	}

	// ------------------------------------------------------------- handlers

	private static ServerPlayer player(CommandContext<CommandSourceStack> ctx) throws CommandSyntaxException {
		return ctx.getSource().getPlayerOrException();
	}

	private static int info(CommandContext<CommandSourceStack> ctx) throws CommandSyntaxException {
		ServerPlayer target = player(ctx);
		ProgressData data = ProgressData.get(target);
		Origin origin = Origins.get(data.origin());
		ctx.getSource().sendSuccess(() -> Component.translatable("command.volunteera.info.origin",
				origin == null ? Origins.noneName() : origin.displayName()), false);
		if (origin != null) {
			ctx.getSource().sendSuccess(() -> Component.translatable("command.volunteera.info.abilities"), false);
			for (Ability ability : origin.abilities()) {
				int level = ProgressManager.effectiveLevel(target, ability);
				String line = "  - " + ability.id().getPath()
						+ ": Lv." + level + "/" + ability.maxLevel()
						+ (ability.isWorldAbility() ? " [world]" : "")
						+ (data.testMode() ? " [test]" : "");
				ctx.getSource().sendSuccess(() -> Component.literal(line), false);
			}
		}
		ctx.getSource().sendSuccess(() -> Component.literal("XP: " + target.experienceLevel), false);
		return 1;
	}

	private static int setOrigin(CommandContext<CommandSourceStack> ctx) throws CommandSyntaxException {
		ServerPlayer target = player(ctx);
		String path = StringArgumentType.getString(ctx, "origin").toLowerCase(Locale.ROOT);
		Identifier originId = "none".equals(path) ? null : Identifier.fromNamespaceAndPath(VolunteeraMod.MOD_ID, path);
		if (originId != null && Origins.get(originId) == null) {
			ctx.getSource().sendFailure(Component.literal("Unknown origin: " + path));
			return 0;
		}
		ProgressManager.setOrigin(target, originId);
		ctx.getSource().sendSuccess(() -> Component.translatable("command.volunteera.origin_switched",
				originId == null ? Origins.noneName() : Origins.get(originId).displayName()), true);
		return 1;
	}

	private static int unlockAbility(CommandContext<CommandSourceStack> ctx) throws CommandSyntaxException {
		ServerPlayer target = player(ctx);
		Ability ability = resolveAbility(ctx);
		if (ability == null) {
			return 0;
		}
		if (ability.isWorldAbility()) {
			ProgressData data = ProgressData.get(target);
			target.setAttached(dev.volunteera.progress.ProgressAttachments.PROGRESS,
					data.withWorldUnlocked(ability.id(), true));
			StateSync.sync(target);
		} else {
			ProgressManager.setAbilityLevel(target, ability.id(), ability.maxLevel());
		}
		// make sure passives come alive immediately
		EventHooks.applyOriginState(target);
		ctx.getSource().sendSuccess(() -> Component.translatable("command.volunteera.unlock.done", ability.id().getPath()), true);
		return 1;
	}

	private static int unlockAll(CommandContext<CommandSourceStack> ctx) throws CommandSyntaxException {
		ServerPlayer target = player(ctx);
		ProgressManager.unlockAll(target);
		EventHooks.applyOriginState(target);
		StateSync.sync(target);
		ctx.getSource().sendSuccess(() -> Component.translatable("command.volunteera.unlockall.done", target.getGameProfile().getName()), true);
		return 1;
	}

	private static int setLevel(CommandContext<CommandSourceStack> ctx) throws CommandSyntaxException {
		ServerPlayer target = player(ctx);
		Ability ability = resolveAbility(ctx);
		if (ability == null) {
			return 0;
		}
		int level = IntegerArgumentType.getInteger(ctx, "level");
		ProgressManager.setAbilityLevel(target, ability.id(), level);
		EventHooks.applyOriginState(target);
		StateSync.sync(target);
		ctx.getSource().sendSuccess(() -> Component.translatable("command.volunteera.setlevel.done",
				ability.id().getPath(), Math.min(level, ability.maxLevel())), true);
		return 1;
	}

	private static int trialProgress(CommandContext<CommandSourceStack> ctx) throws CommandSyntaxException {
		ServerPlayer target = player(ctx);
		Trial trial = resolveTrial(ctx);
		if (trial == null) {
			return 0;
		}
		int amount = IntegerArgumentType.getInteger(ctx, "amount");
		ProgressManager.setTrialProgress(target, trial, amount);
		ctx.getSource().sendSuccess(() -> Component.translatable("command.volunteera.trial.set.done",
				trial.id().getPath(), amount, target.getGameProfile().getName()), true);
		return 1;
	}

	private static int trialComplete(CommandContext<CommandSourceStack> ctx) throws CommandSyntaxException {
		ServerPlayer target = player(ctx);
		Trial trial = resolveTrial(ctx);
		if (trial == null) {
			return 0;
		}
		ProgressManager.setTrialProgress(target, trial, trial.maxProgress());
		ctx.getSource().sendSuccess(() -> Component.translatable("command.volunteera.trial.complete.done",
				trial.id().getPath(), target.getGameProfile().getName()), true);
		return 1;
	}

	private static int refillResources(CommandContext<CommandSourceStack> ctx) throws CommandSyntaxException {
		ServerPlayer target = player(ctx);
		PlayerRuntime rt = RuntimeManager.get(target);
		rt.flightEnergy = FlightController.MAX_ENERGY;
		rt.windCharges = AbilityEngine.windChargeMax(target);
		rt.dousedTicks = 0;
		rt.chargeRefillTicks = 0;
		StateSync.sync(target);
		ctx.getSource().sendSuccess(() -> Component.translatable("command.volunteera.resources.done"), true);
		return 1;
	}

	private static int resetCooldowns(CommandContext<CommandSourceStack> ctx) throws CommandSyntaxException {
		ServerPlayer target = player(ctx);
		RuntimeManager.get(target).cooldownEnds.clear();
		StateSync.sync(target);
		ctx.getSource().sendSuccess(() -> Component.translatable("command.volunteera.resources.done"), true);
		return 1;
	}

	private static int resetProgress(CommandContext<CommandSourceStack> ctx) throws CommandSyntaxException {
		ServerPlayer target = player(ctx);
		ProgressManager.reset(target);
		ctx.getSource().sendSuccess(() -> Component.translatable("command.volunteera.reset.done",
				target.getGameProfile().getName()), true);
		return 1;
	}

	private static int testmode(CommandContext<CommandSourceStack> ctx, boolean on) throws CommandSyntaxException {
		ServerPlayer target = player(ctx);
		ProgressData data = ProgressData.get(target);
		target.setAttached(dev.volunteera.progress.ProgressAttachments.PROGRESS, data.withTestMode(on));
		if (on) {
			ProgressManager.unlockAll(target);
		} else {
			ProgressManager.reset(target);
		}
		EventHooks.applyOriginState(target);
		StateSync.sync(target);
		ctx.getSource().sendSuccess(() -> Component.translatable(
				on ? "command.volunteera.testmode.on" : "command.volunteera.testmode.off",
				target.getGameProfile().getName()), true);
		return 1;
	}

	// ------------------------------------------------------------- resolve

	private static Ability resolveAbility(CommandContext<CommandSourceStack> ctx) {
		String path = StringArgumentType.getString(ctx, "ability").toLowerCase(Locale.ROOT);
		Identifier id = path.contains(":")
				? Identifier.parse(path)
				: Identifier.fromNamespaceAndPath(VolunteeraMod.MOD_ID, path);
		Ability ability = Abilities.get(id);
		if (ability == null) {
			ctx.getSource().sendFailure(Component.literal("Unknown ability: " + path));
			return null;
		}
		return ability;
	}

	private static Trial resolveTrial(CommandContext<CommandSourceStack> ctx) {
		String path = StringArgumentType.getString(ctx, "trial").toLowerCase(Locale.ROOT);
		Identifier id = path.contains(":")
				? Identifier.parse(path)
				: Identifier.fromNamespaceAndPath(VolunteeraMod.MOD_ID, path);
		Trial trial = Trials.get(id);
		if (trial == null) {
			ctx.getSource().sendFailure(Component.literal("Unknown trial: " + path));
			return null;
		}
		return trial;
	}
}
