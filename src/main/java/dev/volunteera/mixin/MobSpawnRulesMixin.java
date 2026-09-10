package dev.volunteera.mixin;

import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EntitySpawnReason;
import net.minecraft.world.entity.Mob;
import net.minecraft.world.level.LevelAccessor;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

import dev.volunteera.server.SpawnSuppression;

/**
 * The only gameplay mixin in the mod: Radiance (and the Deepforge lantern's
 * documented light) blocks *natural* hostile spawns near its holders. This is
 * vanilla's convergence point for natural spawning, so the check is both
 * cheap and complete. Spawn eggs, spawners, commands and events are never
 * suppressed (they use other EntitySpawnReason values).
 */
@Mixin(Mob.class)
public abstract class MobSpawnRulesMixin {
	@Inject(
			method = "checkSpawnRules(Lnet/minecraft/world/level/LevelAccessor;Lnet/minecraft/world/entity/EntitySpawnReason;)Z",
			at = @At("HEAD"),
			cancellable = true
	)
	private void volunteera$suppressRadianceSpawns(LevelAccessor level, EntitySpawnReason reason,
			CallbackInfoReturnable<Boolean> cir) {
		if (reason != EntitySpawnReason.NATURAL) {
			return;
		}
		if (SpawnSuppression.suppresses(level, ((Entity) (Object) this).blockPosition())) {
			cir.setReturnValue(false);
		}
	}
}
