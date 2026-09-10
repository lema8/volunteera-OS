package dev.volunteera.client;

import com.mojang.blaze3d.platform.InputConstants;

import net.minecraft.client.KeyMapping;
import net.minecraft.resources.Identifier;

import org.lwjgl.glfw.GLFW;

import dev.volunteera.VolunteeraMod;
import net.fabricmc.fabric.api.client.keymapping.v1.KeyMappingHelper;

/**
 * Configurable controls through Minecraft's normal keybind system. Six
 * ability slots (matching the HUD chips) plus the menu key - nothing is
 * hardcoded, everything rebinding-friendly.
 */
public final class Keybinds {
	public static KeyMapping.Category CATEGORY;
	public static KeyMapping MENU;
	public static final KeyMapping[] SLOTS = new KeyMapping[6];

	private Keybinds() {
	}

	public static void register() {
		CATEGORY = KeyMapping.Category.register(Identifier.fromNamespaceAndPath(VolunteeraMod.MOD_ID, "main"));
		MENU = KeyMappingHelper.registerKeyMapping(new KeyMapping(
				"key.volunteera.menu", InputConstants.Type.KEYSYM, GLFW.GLFW_KEY_O, CATEGORY));
		int[] defaults = {
				GLFW.GLFW_KEY_Z, GLFW.GLFW_KEY_X, GLFW.GLFW_KEY_C,
				GLFW.GLFW_KEY_V, GLFW.GLFW_KEY_B, GLFW.GLFW_KEY_N
		};
		for (int i = 0; i < SLOTS.length; i++) {
			SLOTS[i] = KeyMappingHelper.registerKeyMapping(new KeyMapping(
					"key.volunteera.ability" + (i + 1), InputConstants.Type.KEYSYM, defaults[i], CATEGORY));
		}
	}
}
