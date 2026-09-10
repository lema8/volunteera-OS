package dev.volunteera.client.hud;

import net.minecraft.client.DeltaTracker;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.Font;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.Identifier;

import dev.volunteera.VolunteeraMod;
import dev.volunteera.api.Ability;
import dev.volunteera.api.Origin;
import dev.volunteera.client.ClientState;
import dev.volunteera.net.Payloads;

/**
 * The in-game HUD: origin label, six ability chips (slot number, name,
 * cooldown seconds, toggle state) plus the flight energy meter and wind
 * charge pips for Skyborne. Compact, readable at any GUI scale, and it never
 * renders when there is nothing to show.
 */
public final class OriginsHud {
	private static final int CHIP_WIDTH = 62;
	private static final int CHIP_HEIGHT = 12;
	private static final int CHIP_GAP = 2;
	private static final int CHIPS = 6;

	private OriginsHud() {
	}

	public static void register() {
		// Below the status bars, above the hotbar area's left side.
		net.fabricmc.fabric.api.client.rendering.v1.hud.HudElementRegistry.attachElementAfter(
				net.fabricmc.fabric.api.client.rendering.v1.hud.VanillaHudElements.INFO_BAR,
				Identifier.fromNamespaceAndPath(VolunteeraMod.MOD_ID, "hud"),
				(graphics, deltaTracker) -> extract(graphics, deltaTracker));
	}

	private static void extract(GuiGraphicsExtractor g, DeltaTracker delta) {
		Minecraft mc = Minecraft.getInstance();
		if (mc.player == null || mc.options.hideGui) {
			return;
		}
		Origin origin = ClientState.origin();
		if (origin == null) {
			return;
		}
		Font font = mc.font;

		int baseY = g.guiHeight() - 54;
		int x = 4;

		// Origin label
		String originName = origin.displayName().getString();
		g.fill(x, baseY - 11, x + font.width(originName) + 4, baseY - 1, 0x66000000);
		g.text(font, originName, x + 2, baseY - 9, origin.color() | 0xFF000000);

		// Ability chips (two rows of three to keep the footprint small)
		var abilities = ClientState.abilitiesSafe();
		int drawn = 0;
		for (int slot = 0; slot < CHIPS && drawn < abilities.size(); slot++) {
			Identifier bound = origin.hudSlot(slot);
			if (bound == null) {
				continue;
			}
			ClientState.AbilityView view = null;
			for (var a : abilities) {
				if (a.id().equals(bound)) {
					view = a;
					break;
				}
			}
			if (view == null) {
				continue;
			}
			int col = drawn % 3;
			int row = drawn / 3;
			int cx = x + col * (CHIP_WIDTH + CHIP_GAP);
			int cy = baseY + row * (CHIP_HEIGHT + CHIP_GAP);
			drawChip(g, font, cx, cy, slot + 1, origin.ability(bound), view);
			drawn++;
		}

		drawFlightMeter(g, font, origin);
	}

	private static void drawChip(GuiGraphicsExtractor g, Font font, int x, int y, int slot,
			Ability ability, ClientState.AbilityView view) {
		int bg = 0x99000000;
		if (view.active()) {
			bg = 0x992A6A2A; // active toggle: green tint
		} else if (view.level() <= 0) {
			bg = 0x66333333; // locked: dim
		}
		g.fill(x, y, x + CHIP_WIDTH, y + CHIP_HEIGHT, bg);
		g.fill(x, y, x + 7, y + CHIP_HEIGHT, view.level() > 0 ? 0xFF555555 : 0xFF2A2A2A);
		g.text(font, Integer.toString(slot), x + 2, y + 2, 0xFFFFFFFF);

		float cd = ClientState.cooldownSecondsRemaining(view);
		String label;
		int color;
		if (view.level() <= 0) {
			label = shorten(font, ability.nameKey(), CHIP_WIDTH - 10);
			color = 0xFF777777;
		} else if (cd > 0) {
			label = String.format("%.0fs", cd);
			color = 0xFFFFAA00;
		} else if (view.active()) {
			label = "ON";
			color = 0xFF7CFC6A;
		} else {
			label = shorten(font, Component.translatable(ability.nameKey()).getString(), CHIP_WIDTH - 10);
			color = 0xFFE8E8E8;
		}
		g.text(font, label, x + 9, y + 2, color);
	}

	private static String shorten(Font font, String text, int maxWidth) {
		if (font.width(text) <= maxWidth) {
			return text;
		}
		while (text.length() > 1 && font.width(text + "…") > maxWidth) {
			text = text.substring(0, text.length() - 1);
		}
		return text + "…";
	}

	private static void drawFlightMeter(GuiGraphicsExtractor g, Font font, Origin origin) {
		boolean skyborne = origin.id().getPath().equals("skyborne");
		float energy = ClientState.flightEnergy;
		int phase = ClientState.flightPhase;

		if (!skyborne) {
			return;
		}
		boolean relevant = phase != 0 || energy < 99.0f;
		if (!relevant) {
			return;
		}

		int width = 91;
		int x = g.guiWidth() / 2 - width / 2;
		int y = g.guiHeight() - 49;

		g.fill(x - 1, y - 1, x + width + 1, y + 6, 0x88000000);
		g.fill(x, y, x + width, y + 4, 0xFF222222);

		float fraction = energy / 100.0f;
		int fillWidth = (int) (width * fraction);
		int color = 0xFF55CBFF;
		if (fraction < 0.25f) {
			long blink = (System.currentTimeMillis() / 250) % 2;
			color = blink == 0 ? 0xFFFF5555 : 0xFF8A2020;
		} else if (fraction < 0.5f) {
			color = 0xFFFFCC55;
		}
		if (fillWidth > 0) {
			g.fill(x, y, x + fillWidth, y + 4, color);
		}

		String label = phase == 1
				? Component.translatable("message.volunteera.status_energy").getString()
				: String.format("%s %d%%", Component.translatable("message.volunteera.status_energy").getString(), (int) energy);
		g.text(font, label, x, y - 10, 0xFFCFE9FF);

		// Wind charge pips
		if (ClientState.windChargeMax > 0) {
			int pipX = x + width + 6;
			for (int i = 0; i < ClientState.windChargeMax; i++) {
				int c = i < ClientState.windCharges ? 0xFFB9F2FF : 0xFF3A4A50;
				g.fill(pipX + i * 5, y, pipX + i * 5 + 3, y + 4, c);
			}
		}

		if (ClientState.doused) {
			g.text(font, "✿", x + width + 2 + ClientState.windChargeMax * 5 + 4, y - 2, 0xFF7FB2FF);
		}
	}
}
