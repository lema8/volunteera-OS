package dev.volunteera.client.screen;

import java.util.ArrayList;
import java.util.List;

import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.network.chat.Component;

import dev.volunteera.api.Abilities;
import dev.volunteera.api.Ability;
import dev.volunteera.api.Origin;
import dev.volunteera.api.Trial;
import dev.volunteera.client.ClientState;
import dev.volunteera.net.Payloads;

/**
 * The Origins & Abilities menu. Left: origin choice. Right: every ability of
 * the current origin with live state; click an ability for its full details
 * (description, level, costs, cooldown, unlock requirement, trial progress).
 * Built entirely from vanilla widgets and immediate-mode text - no custom
 * mouse handling, resolution-proof.
 */
public final class OriginsScreen extends Screen {
	private static final int PANE_X = 118;
	private static final int ROW_HEIGHT = 18;
	private static final int MAX_ROWS = 10;

	private final List<Button> abilityButtons = new ArrayList<>();
	private int selectedAbility = 0;
	private int abilityCount;
	private Origin lastOrigin;

	public OriginsScreen() {
		super(Component.translatable("gui.volunteera.menu.title"));
	}

	@Override
	protected void init() {
		int y = 34;
		for (Origin origin : dev.volunteera.api.Origins.all()) {
			Button button = Button.builder(origin.displayName(), b -> chooseOrigin(origin.id()))
					.bounds(8, y, 100, 18)
					.build();
			this.addRenderableWidget(button);
			y += 20;
		}
		Button unbound = Button.builder(Component.translatable("origin.volunteera.none"), b -> chooseOrigin(null))
				.bounds(8, y, 100, 18)
				.build();
		this.addRenderableWidget(unbound);

		for (int i = 0; i < MAX_ROWS; i++) {
			final int index = i;
			Button button = Button.builder(Component.literal(""), b -> this.selectedAbility = index)
					.bounds(PANE_X, 0, Math.min(180, this.width - PANE_X - 8), ROW_HEIGHT - 2)
					.build();
			button.visible = false;
			this.abilityButtons.add(button);
			this.addRenderableWidget(button);
		}
		refreshAbilityButtons();
	}

	private void chooseOrigin(net.minecraft.resources.Identifier originId) {
		if (this.minecraft != null && this.minecraft.player != null) {
			net.fabricmc.fabric.api.client.networking.v1.ClientPlayNetworking.send(
					new Payloads.SetOriginPayload(originId));
		}
	}

	@Override
	public void extractRenderState(GuiGraphicsExtractor g, int mouseX, int mouseY, float delta) {
		super.extractRenderState(g, mouseX, mouseY, delta);
		if (this.minecraft == null || this.minecraft.player == null) {
			return;
		}

		g.centeredText(this.font, this.title, this.width / 2, 8, 0xFFFFFFFF);

		Origin origin = ClientState.origin();
		if (lastOrigin != origin) {
			lastOrigin = origin;
			selectedAbility = 0;
			refreshAbilityButtons();
		}

		// Origin description under the buttons.
		if (origin != null) {
			drawWrapped(g, Component.translatable(origin.descKey()).getString(),
					8, 130, 104, 0xFFBBBBBB);
		} else {
			drawWrapped(g, Component.translatable("origin.volunteera.none.desc").getString(),
					8, 130, 104, 0xFFBBBBBB);
		}

		// Detail panel for the selected ability.
		List<ClientState.AbilityView> views = ClientState.abilitiesSafe();
		if (origin != null && !views.isEmpty() && selectedAbility < views.size()) {
			drawAbilityDetails(g, origin, views.get(selectedAbility));
		}
	}

	private void refreshAbilityButtons() {
		Origin origin = ClientState.origin();
		List<ClientState.AbilityView> views = ClientState.abilitiesSafe();
		abilityCount = views.size();
		for (int i = 0; i < abilityButtons.size(); i++) {
			Button button = abilityButtons.get(i);
			if (origin == null || i >= views.size() || i >= MAX_ROWS) {
				button.visible = false;
				continue;
			}
			ClientState.AbilityView view = views.get(i);
			Ability ability = Abilities.get(view.id());
			if (ability == null) {
				button.visible = false;
				continue;
			}
			String name = Component.translatable(ability.nameKey()).getString();
			String status = view.level() > 0 ? "Lv." + view.level() : "🔒";
			button.setMessage(Component.literal(name + "  " + status));
			button.setPosition(PANE_X, 26 + i * ROW_HEIGHT);
			button.setWidth(Math.min(180, this.width - PANE_X - 8));
			button.visible = true;
		}
	}

	private void drawAbilityDetails(GuiGraphicsExtractor g, Origin origin, ClientState.AbilityView view) {
		Ability ability = Abilities.get(view.id());
		if (ability == null) {
			return;
		}
		int paneWidth = this.width - PANE_X - 8;
		int x = PANE_X;
		int y = 26 + MAX_ROWS * ROW_HEIGHT + 6;

		// Header: name + kind + level
		String name = Component.translatable(ability.nameKey()).getString();
		String kind = switch (ability.kind()) {
			case ACTIVE -> "Active";
			case TOGGLE -> "Toggle";
			case PASSIVE -> "Passive";
		};
		String levelText;
		if (view.level() > 0) {
			levelText = (ability.maxLevel() > 1 ? "  Lv." + view.level() + "/" + ability.maxLevel() : "")
					+ (view.active() ? "  [ON]" : "");
		} else {
			levelText = "  [locked]";
		}
		g.text(this.font, name, x, y, origin.color() | 0xFF000000);
		g.text(this.font, kind + levelText, x, y + 11, 0xFF999999);
		y += 26;

		// Description (wrapped)
		y = drawWrapped(g, Component.translatable(ability.descKey()).getString(), x, y, paneWidth, 0xFFDDDDDD) + 4;

		// Meta: cost, cooldown, unlock requirement
		if (view.level() > 0) {
			var cost = ability.cost(view.level());
			if (!cost.isEmpty()) {
				g.text(this.font, "Cost: " + costDescription(cost), x, y, 0xFFCC8844);
				y += 11;
			}
			float cd = ability.cooldownSeconds(view.level());
			if (cd > 0) {
				float remaining = ClientState.cooldownSecondsRemaining(view);
				String cdText = remaining > 0
						? String.format("Cooldown: %.1fs left", remaining)
						: String.format("Cooldown: %.0fs", cd);
				g.text(this.font, cdText, x, y, remaining > 0 ? 0xFFFFAA00 : 0xFF888888);
				y += 11;
			}
			if (ClientState.windChargeMax > 0 && ability.id().getPath().contains("gale")) {
				g.text(this.font, "Charges: " + ClientState.windCharges + "/" + ClientState.windChargeMax,
						x, y, 0xFFB9F2FF);
				y += 11;
			}
		} else {
			// Locked: show requirement
			if (ability.isWorldAbility()) {
				Trial trial = ability.worldTrial();
				g.text(this.font, Component.translatable("gui.volunteera.world_ability").getString()
						+ " - " + Component.translatable(trial.nameKey()).getString(), x, y, 0xFF66D9E8);
				y += 11;
				ClientState.TrialView progress = findTrial(trial.id());
				String progressText = progress != null && progress.progress() >= progress.target()
						? Component.translatable("gui.volunteera.trial_done").getString()
						: Component.translatable(trial.descKey()).getString()
								+ (progress != null ? "  (" + progress.progress() + "/" + progress.target() + ")" : "");
				y = drawWrapped(g, progressText, x, y, paneWidth, 0xFFAAAAAA) + 2;
			} else {
				int threshold = ability.unlockXpFor(1);
				if (threshold > 0) {
					g.text(this.font, Component.translatable("gui.volunteera.upgrades_at", threshold).getString(),
							x, y, 0xFF888888);
					y += 11;
				}
			}
			y = drawWrapped(g, Component.translatable("gui.volunteera.locked").getString()
					+ " - " + Component.translatable("gui.volunteera.hud_slot", ability.hudSlot() + 1).getString(),
					x, y, paneWidth, 0xFF666666) + 2;
		}
	}

	private String costDescription(dev.volunteera.api.Cost cost) {
		StringBuilder sb = new StringBuilder();
		if (cost.hunger() > 0) {
			sb.append(cost.hunger()).append(" hunger");
		}
		if (cost.health() > 0) {
			if (sb.length() > 0) {
				sb.append(" + ");
			}
			sb.append((int) cost.health()).append(" HP");
		}
		if (cost.item() != null) {
			if (sb.length() > 0) {
				sb.append(" + ");
			}
			sb.append("1 ").append(Component.translatable(cost.item().getDescriptionId()).getString());
		}
		return sb.toString();
	}

	private ClientState.TrialView findTrial(net.minecraft.resources.Identifier id) {
		for (ClientState.TrialView view : ClientState.trials) {
			if (view.id().equals(id)) {
				return view;
			}
		}
		return null;
	}

	private int drawWrapped(GuiGraphicsExtractor g, String text, int x, int y, int maxWidth, int color) {
		String[] words = text.split(" ");
		StringBuilder line = new StringBuilder();
		for (String word : words) {
			String candidate = line.isEmpty() ? word : line + " " + word;
			if (this.font.width(candidate) > maxWidth && !line.isEmpty()) {
				g.text(this.font, line.toString(), x, y, color);
				y += 11;
				line = new StringBuilder(word);
			} else {
				line = new StringBuilder(candidate);
			}
		}
		if (!line.isEmpty()) {
			g.text(this.font, line.toString(), x, y, color);
			y += 11;
		}
		return y;
	}

	@Override
	public void onClose() {
		if (this.minecraft != null) {
			this.minecraft.setScreen(null);
		}
	}
}
