package dev.volunteera.net;

import net.minecraft.network.FriendlyByteBuf;
import net.minecraft.network.codec.StreamCodec;
import net.minecraft.network.protocol.common.custom.CustomPacketPayload;
import net.minecraft.resources.Identifier;

import dev.volunteera.VolunteeraMod;

/**
 * All custom payloads. Codecs are hand-rolled with the small, stable
 * FriendlyByteBuf surface (UTF strings + varints), which keeps the version
 * surface minimal.
 *
 * <p>Client → Server payloads are pure requests; the server re-validates
 * everything. The Server → Client payload is a full authoritative snapshot.
 */
public final class Payloads {
	private Payloads() {
	}

	public record UseAbilityPayload(Identifier abilityId) implements CustomPacketPayload {
		public static final Type<UseAbilityPayload> TYPE =
				new Type<>(Identifier.fromNamespaceAndPath(VolunteeraMod.MOD_ID, "use_ability"));
		public static final StreamCodec<FriendlyByteBuf, UseAbilityPayload> CODEC = StreamCodec.ofMember(
				(buf, p) -> buf.writeIdentifier(p.abilityId),
				buf -> new UseAbilityPayload(buf.readIdentifier()));

		@Override
		public Type<? extends CustomPacketPayload> type() {
			return TYPE;
		}
	}

	public record SetOriginPayload(Identifier originId) implements CustomPacketPayload {
		public static final Type<SetOriginPayload> TYPE =
				new Type<>(Identifier.fromNamespaceAndPath(VolunteeraMod.MOD_ID, "set_origin"));
		public static final StreamCodec<FriendlyByteBuf, SetOriginPayload> CODEC = StreamCodec.ofMember(
				(buf, p) -> {
					buf.writeBoolean(p.originId != null);
					if (p.originId != null) {
						buf.writeIdentifier(p.originId);
					}
				},
				buf -> new SetOriginPayload(buf.readBoolean() ? buf.readIdentifier() : null));

		@Override
		public Type<? extends CustomPacketPayload> type() {
			return TYPE;
		}
	}

	public record RequestSyncPayload() implements CustomPacketPayload {
		public static final RequestSyncPayload INSTANCE = new RequestSyncPayload();
		public static final Type<RequestSyncPayload> TYPE =
				new Type<>(Identifier.fromNamespaceAndPath(VolunteeraMod.MOD_ID, "request_sync"));
		public static final StreamCodec<FriendlyByteBuf, RequestSyncPayload> CODEC = StreamCodec.unit(INSTANCE);

		@Override
		public Type<? extends CustomPacketPayload> type() {
			return TYPE;
		}
	}

	/** One ability entry in the sync snapshot. */
	public record AbilityState(Identifier id, int level, boolean active, int cooldownTicks) {
	}

	public record TrialState(Identifier id, int progress, int target) {
	}

	public record SyncStatePayload(
			Identifier originId,
			java.util.List<AbilityState> abilities,
			java.util.List<TrialState> trials,
			float flightEnergy,
			byte flightPhase,
			int windCharges,
			int windChargeMax,
			boolean doused) implements CustomPacketPayload {

		public static final Type<SyncStatePayload> TYPE =
				new Type<>(Identifier.fromNamespaceAndPath(VolunteeraMod.MOD_ID, "sync_state"));

		public static final StreamCodec<FriendlyByteBuf, SyncStatePayload> CODEC = StreamCodec.ofMember(
				SyncStatePayload::write,
				SyncStatePayload::read);

		private static void write(FriendlyByteBuf buf, SyncStatePayload p) {
			buf.writeBoolean(p.originId != null);
			if (p.originId != null) {
				buf.writeIdentifier(p.originId);
			}
			buf.writeVarInt(p.abilities.size());
			for (AbilityState a : p.abilities) {
				buf.writeIdentifier(a.id);
				buf.writeVarInt(a.level);
				buf.writeBoolean(a.active);
				buf.writeVarInt(a.cooldownTicks);
			}
			buf.writeVarInt(p.trials.size());
			for (TrialState t : p.trials) {
				buf.writeIdentifier(t.id);
				buf.writeVarInt(t.progress);
				buf.writeVarInt(t.target);
			}
			buf.writeFloat(p.flightEnergy);
			buf.writeByte(p.flightPhase);
			buf.writeVarInt(p.windCharges);
			buf.writeVarInt(p.windChargeMax);
			buf.writeBoolean(p.doused);
		}

		private static SyncStatePayload read(FriendlyByteBuf buf) {
			Identifier origin = buf.readBoolean() ? buf.readIdentifier() : null;
			int abilityCount = buf.readVarInt();
			java.util.List<AbilityState> abilities = new java.util.ArrayList<>(abilityCount);
			for (int i = 0; i < abilityCount; i++) {
				abilities.add(new AbilityState(buf.readIdentifier(), buf.readVarInt(), buf.readBoolean(), buf.readVarInt()));
			}
			int trialCount = buf.readVarInt();
			java.util.List<TrialState> trials = new java.util.ArrayList<>(trialCount);
			for (int i = 0; i < trialCount; i++) {
				trials.add(new TrialState(buf.readIdentifier(), buf.readVarInt(), buf.readVarInt()));
			}
			return new SyncStatePayload(origin, abilities, trials,
					buf.readFloat(), buf.readByte(), buf.readVarInt(), buf.readVarInt(), buf.readBoolean());
		}

		@Override
		public Type<? extends CustomPacketPayload> type() {
			return TYPE;
		}
	}
}
