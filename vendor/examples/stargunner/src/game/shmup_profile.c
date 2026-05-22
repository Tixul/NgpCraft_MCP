#include "ngpc_flash.h"
#include "ngpc_log.h"
#include "shmup_profile.h"

#define SHMUP_SAVE_VERSION 1u

typedef struct {
    char name[3];
    u8 score_lo;
    u8 score_hi;
    u8 flags;
} SaveHighScoreEntry;

typedef struct {
    u8 magic[4];
    u8 version;
    u8 continue_setting;
    u8 reserved0;
    u8 reserved1;
    SaveHighScoreEntry scores[SHMUP_HIGHSCORE_COUNT];
    u8 checksum;
    u8 padding[SAVE_SIZE - 69];
} ShmupSaveData;

static ShmupSaveData s_save;
static u8 s_save_dirty = 0u;

static u16 save_entry_score_get(const SaveHighScoreEntry *e)
{
    return (u16)((u16)e->score_lo | ((u16)e->score_hi << 8));
}

static void save_entry_score_set(SaveHighScoreEntry *e, u16 score)
{
    e->score_lo = (u8)(score & 0xFFu);
    e->score_hi = (u8)(score >> 8);
}

static u8 save_checksum_compute(const ShmupSaveData *save)
{
    const u8 *raw = (const u8 *)save;
    u8 sum = 0u;
    u16 i;

    for (i = 0; i < (SAVE_SIZE - 1u); i++) {
        sum = (u8)(sum + raw[i]);
    }
    /* The checksum field (byte 68) falls within the loop range and is
     * self-referential. Subtract it out so the digest covers only data. */
    sum = (u8)(sum - save->checksum);
    return (u8)(sum ^ 0x5Au);
}

static void save_defaults_set(void)
{
    u8 i;

    s_save.magic[0] = 0xCA;
    s_save.magic[1] = 0xFE;
    s_save.magic[2] = 0x20;
    s_save.magic[3] = 0x26;
    s_save.version = SHMUP_SAVE_VERSION;
    s_save.continue_setting = 3u;
    s_save.reserved0 = 0u;
    s_save.reserved1 = 0u;

    for (i = 0; i < SHMUP_HIGHSCORE_COUNT; i++) {
        s_save.scores[i].name[0] = '-';
        s_save.scores[i].name[1] = '-';
        s_save.scores[i].name[2] = '-';
        save_entry_score_set(&s_save.scores[i], 0u);
        s_save.scores[i].flags = 0u;
    }

    for (i = 0; i < (u8)sizeof(s_save.padding); i++) {
        s_save.padding[i] = 0u;
    }

    s_save.checksum = save_checksum_compute(&s_save);
}

static u8 save_is_valid(const ShmupSaveData *save)
{
    if (save->magic[0] != 0xCA || save->magic[1] != 0xFE ||
        save->magic[2] != 0x20 || save->magic[3] != 0x26) {
        return 0u;
    }
    if (save->version != SHMUP_SAVE_VERSION) {
        return 0u;
    }
    if (save->continue_setting > 10u) {
        return 0u;
    }
    if (save->checksum != save_checksum_compute(save)) {
        return 0u;
    }
    return 1u;
}

static void save_commit(void)
{
    /* FSAV: confirm save_commit was reached and which continue value is being written */
    NGPC_LOG_HEX("FSAV", s_save.continue_setting);
    s_save.checksum = save_checksum_compute(&s_save);
    ngpc_flash_save(&s_save);
    NGPC_LOG_HEX("FWRT", ngpc_flash_exists());
    /* FVFY: 0=flash matches exactly, >0=N bytes wrong (write incomplete/failed) */
    NGPC_LOG_HEX("FVFY", ngpc_flash_verify(&s_save));
    s_save_dirty = 0u;
}

void shmup_profile_init(void)
{
    ngpc_flash_init();

    /* Essai 8: boot diagnostics — distinguish "data not retained" vs "validation fails" */
    if (ngpc_flash_exists()) {
        NGPC_LOG_HEX("FEXS", 1u);
        ngpc_flash_load(&s_save);
        /* CKSM: hi=stored, lo=computed — match => checksum OK, mismatch => data corrupt */
        NGPC_LOG_HEX("CKSM", (u16)(((u16)s_save.checksum << 8) | save_checksum_compute(&s_save)));
        NGPC_LOG_HEX("VERS", s_save.version);
        NGPC_LOG_HEX("CONT", s_save.continue_setting);
        if (save_is_valid(&s_save)) {
            s_save_dirty = 0u;
            return;
        }
        NGPC_LOG_HEX("INVL", 1u); /* reached only if validation fails */
    } else {
        NGPC_LOG_HEX("FEXS", 0u); /* flash not found on boot */
    }

    save_defaults_set();
    s_save_dirty = 0u;
}

u8 shmup_profile_continue_setting_get(void)
{
    return s_save.continue_setting;
}

void shmup_profile_continue_setting_set(u8 value)
{
    if (value > 10u) value = 10u;
    if (s_save.continue_setting == value) return;
    s_save.continue_setting = value;
    s_save_dirty = 1u;
}

void shmup_profile_flush(void)
{
    if (!s_save_dirty) return;
    save_commit();
}

void shmup_profile_highscore_get(u8 index, ShmupHighScore *out_entry)
{
    if (!out_entry) return;

    if (index >= SHMUP_HIGHSCORE_COUNT) {
        out_entry->name[0] = '-';
        out_entry->name[1] = '-';
        out_entry->name[2] = '-';
        out_entry->name[3] = '\0';
        out_entry->score = 0u;
        return;
    }

    out_entry->name[0] = s_save.scores[index].name[0];
    out_entry->name[1] = s_save.scores[index].name[1];
    out_entry->name[2] = s_save.scores[index].name[2];
    out_entry->name[3] = '\0';
    out_entry->score = save_entry_score_get(&s_save.scores[index]);
}

u8 shmup_profile_highscore_qualifies(u16 score)
{
    return (u8)(score >= save_entry_score_get(&s_save.scores[SHMUP_HIGHSCORE_COUNT - 1u]));
}

u8 shmup_profile_highscore_submit(const char name[3], u16 score)
{
    SaveHighScoreEntry entry;
    u8 i;
    u8 insert_at = SHMUP_HIGHSCORE_COUNT;

    for (i = 0; i < SHMUP_HIGHSCORE_COUNT; i++) {
        if (score >= save_entry_score_get(&s_save.scores[i])) {
            insert_at = i;
            break;
        }
    }

    if (insert_at >= SHMUP_HIGHSCORE_COUNT) {
        return 0u;
    }

    entry.name[0] = name[0];
    entry.name[1] = name[1];
    entry.name[2] = name[2];
    save_entry_score_set(&entry, score);
    entry.flags = 0u;

    for (i = (u8)(SHMUP_HIGHSCORE_COUNT - 1u); i > insert_at; i--) {
        s_save.scores[i] = s_save.scores[i - 1u];
    }
    s_save.scores[insert_at] = entry;
    s_save_dirty = 1u;
    save_commit();
    return 1u;
}
