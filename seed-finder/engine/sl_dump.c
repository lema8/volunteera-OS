/* sl_dump.c — emits js/data-generated.js: the authoritative 26.2 biome
 * catalog (names + AMIDST-style colors) and structure configs, straight
 * from cubiomes. Run: ./sl_dump > ../js/data-generated.js */
#include <stdio.h>
#include <stdint.h>

#include "seedlab.c"

static const char *struct_name(int t)
{
    switch (t) {
    case Desert_Pyramid: return "Desert Pyramid";
    case Jungle_Temple:  return "Jungle Temple";
    case Swamp_Hut:      return "Swamp Hut";
    case Igloo:          return "Igloo";
    case Village:        return "Village";
    case Ocean_Ruin:     return "Ocean Ruins";
    case Shipwreck:      return "Shipwreck";
    case Monument:       return "Ocean Monument";
    case Mansion:        return "Woodland Mansion";
    case Outpost:        return "Pillager Outpost";
    case Ruined_Portal:  return "Ruined Portal";
    case Ruined_Portal_N:return "Ruined Portal (Nether)";
    case Ancient_City:   return "Ancient City";
    case Treasure:       return "Buried Treasure";
    case Mineshaft:      return "Mineshaft";
    case Geode:          return "Amethyst Geode";
    case Fortress:       return "Nether Fortress";
    case Bastion:        return "Bastion Remnant";
    case End_City:       return "End City";
    case Trail_Ruins:    return "Trail Ruins";
    case Trial_Chambers: return "Trial Chambers";
    default: return "?";
    }
}

int main(void)
{
    int mc = sl_init(0);
    unsigned char colors[256][3];
    initBiomeColors(colors);

    printf("/* AUTO-GENERATED from cubiomes (MC %s) by engine/sl_dump.c — do not edit. */\n",
           mc == MC_NEWEST ? "26.2" : "?");
    printf("export const MC_VERSION_ID = %d;\n", mc);
    printf("export const MC_VERSION_STRING = \"%s\";\n\n", mc2str(mc));

    printf("export const BIOMES = [\n");
    for (int id = 0; id < 256; id++) {
        if (!biomeExists(mc, id))
            continue;
        const char *n = biome2str(mc, id);
        printf("  { id: %d, name: \"%s\", color: \"#%02x%02x%02x\" },\n",
               id, n, colors[id][0], colors[id][1], colors[id][2]);
    }
    printf("];\n\n");

    printf("export const STRUCT_TYPES = {\n");
    const int types[] = { Village, Outpost, Mansion, Monument,
        Desert_Pyramid, Jungle_Temple, Swamp_Hut, Igloo, Ocean_Ruin,
        Shipwreck, Ruined_Portal, Ancient_City, Trail_Ruins, Trial_Chambers,
        Treasure, Geode, Mineshaft, Fortress, Bastion, Ruined_Portal_N,
        End_City };
    for (size_t i = 0; i < sizeof(types)/sizeof(types[0]); i++) {
        StructureConfig sc;
        int ok = getStructureConfig(types[i], mc, &sc);
        printf("  %d: { name: \"%s\", ok: %d, region: %d, dim: %d },\n",
               types[i], struct_name(types[i]), ok,
               ok ? sc.regionSize : 0, ok ? sc.dim : 0);
    }
    printf("};\n");
    return 0;
}
