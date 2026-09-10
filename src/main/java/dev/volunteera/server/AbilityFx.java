package dev.volunteera.server;

import java.util.List;
import java.util.function.Predicate;

import net.minecraft.core.Holder;
import net.minecraft.core.particles.ParticleTypes;
import net.minecraft.network.chat.Component;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.sounds.SoundEvent;
import net.minecraft.sounds.SoundSource;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.Vec3;

import dev.volunteera.config.ModConfig;

/**
 * Server-side combat and presentation helpers shared by abilities: area
 * damage with knockback, ignition, sounds and particles. All helpers are
 * no-throw: an ability that fails halfway still leaves a consistent world.
 */
public final class AbilityFx {
	private AbilityFx() {
	}

	/** Damages and knocks back every valid target around a center point. */
	public static int areaAttack(ServerPlayer owner, ServerLevel level, Vec3 center, double radius,
			float damage, double knockback, int igniteSeconds) {
		return areaAttack(owner, level, center, radius, damage, knockback, igniteSeconds, target -> true);
	}

	public static int areaAttack(ServerPlayer owner, ServerLevel level, Vec3 center, double radius,
			float damage, double knockback, int igniteSeconds, Predicate<LivingEntity> filter) {
		AABB box = new AABB(center.x - radius, center.y - radius, center.z - radius,
				center.x + radius, center.y + radius, center.z + radius);
		List<LivingEntity> targets = level.getEntitiesOfClass(LivingEntity.class, box,
				e -> e != owner && e.isAlive() && e.isAttackable() && filter.test(e));
		float scaledDamage = damage * ModConfig.get().damageMultiplier;
		int hits = 0;
		for (LivingEntity target : targets) {
			if (target instanceof net.minecraft.world.entity.player.Player && !ModConfig.get().allowPvp) {
				continue;
			}
			target.hurtServer(level, level.damageSources().indirectMagic(owner, owner), scaledDamage);
			pushFrom(target, center, knockback);
			if (igniteSeconds > 0) {
				target.setRemainingFireTicks(igniteSeconds * 20);
			}
			hits++;
		}
		return hits;
	}

	/** Impulse away from a center; works on other players via the velocity sync flag. */
	public static void pushFrom(LivingEntity target, Vec3 center, double knockback) {
		if (knockback <= 0) {
			return;
		}
		Vec3 away = target.position().subtract(center);
		double horizontal = Math.sqrt(away.x * away.x + away.z * away.z);
		if (horizontal < 0.01) {
			away = new Vec3(0, away.y, 1);
			horizontal = 1;
		}
		double strength = knockback / Math.max(1.0, horizontal);
		Vec3 push = new Vec3(away.x * strength, Math.min(0.6, knockback * 0.35), away.z * strength);
		target.setDeltaMovement(target.getDeltaMovement().add(push));
		target.hurtMarked = true;
	}

	public static void playSound(ServerLevel level, Vec3 pos, SoundEvent sound, float volume, float pitch) {
		level.playSound(null, pos.x, pos.y, pos.z, sound, SoundSource.PLAYERS, volume, pitch);
	}

	public static void playSound(ServerLevel level, Vec3 pos, Holder<net.minecraft.sounds.SoundEvent> sound, float volume, float pitch) {
		if (sound != null) {
			level.playSound(null, pos.x, pos.y, pos.z, sound.value(), SoundSource.PLAYERS, volume, pitch);
		}
	}

	public static void playSound(ServerLevel level, net.minecraft.core.BlockPos pos, Holder<net.minecraft.sounds.SoundEvent> sound, float volume, float pitch) {
		if (sound != null) {
			level.playSound(null, pos, sound.value(), SoundSource.PLAYERS, volume, pitch);
		}
	}

	/** Server-side velocity change that reaches the owning client. */
	public static void setVelocity(net.minecraft.world.entity.Entity entity, Vec3 velocity) {
		entity.setDeltaMovement(velocity);
		entity.hurtMarked = true;
	}

	public static void ringParticles(ServerLevel level, Vec3 center, double radius,
			net.minecraft.core.particles.SimpleParticleType type, int perRing) {
		int rings = 2;
		for (int ring = 0; ring < rings; ring++) {
			double r = radius * (ring + 1) / rings;
			int count = Math.max(8, perRing);
			for (int i = 0; i < count; i++) {
				double angle = Math.PI * 2 * i / count;
				double x = center.x + Math.cos(angle) * r;
				double z = center.z + Math.sin(angle) * r;
				level.sendParticles(type, x, center.y + 0.2, z, 1, 0, 0.02, 0, 0.0);
			}
		}
	}

	public static void burstParticles(ServerLevel level, Vec3 pos, net.minecraft.core.particles.SimpleParticleType type, int count, double spread) {
		level.sendParticles(type, pos.x, pos.y, pos.z, count, spread, spread, spread, 0.1);
	}

	public static void actionbar(ServerPlayer player, Component message) {
		player.connection.send(new net.minecraft.network.protocol.game.ClientboundSetActionBarTextPacket(message));
	}
}
