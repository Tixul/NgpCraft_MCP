# NGPC Template 2026 - Makefile
# MIT License
#
# Targets: make / make clean / make move_files
# Toolchain: Toshiba cc900 (TLCS-900/H C compiler)
#
# Build outputs:
# - Objects (.rel): build/obj/
# - Link intermediates: project root (tulink/tuconv always write to cwd)
#   move_files cleans them up to build/tmp/
# - Final ROMs: bin/

NAME = main
OBJ_DIR = build/obj
TMP_DIR = build/tmp
OUTPUT_DIR = bin
PYTHON = py -3.11
CC900_CPU ?= -Nb2

# ---- Feature flags (sync with src/core/ngpc_config.h defaults) ----
NGP_ENABLE_SOUND ?= 1
NGP_ENABLE_FLASH_SAVE ?= 1
NGP_ENABLE_DEBUG ?= 0
NGP_ENABLE_DMA ?= 1
NGP_ENABLE_SPRMUX ?= 0
NGP_ENABLE_SPR_SHADOW ?= 0
NGP_ENABLE_PROFILER ?= 0
NGP_PROFILE_RELEASE ?= 0
NGP_DMA_ALLOW_VBLANK_TRIGGER ?= 0
NGP_DMA_INSTALL_DONE_ISR ?= 0
NGP_DMA_INSTALL_REARM_ISR ?= 0

CDEFS = \
	-DNGP_ENABLE_SOUND=$(NGP_ENABLE_SOUND) \
	-DNGP_ENABLE_FLASH_SAVE=$(NGP_ENABLE_FLASH_SAVE) \
	-DNGP_ENABLE_DEBUG=$(NGP_ENABLE_DEBUG) \
	-DNGP_ENABLE_DMA=$(NGP_ENABLE_DMA) \
	-DNGP_ENABLE_SPRMUX=$(NGP_ENABLE_SPRMUX) \
	-DNGP_ENABLE_SPR_SHADOW=$(NGP_ENABLE_SPR_SHADOW) \
	-DNGP_ENABLE_PROFILER=$(NGP_ENABLE_PROFILER) \
	-DNGP_PROFILE_RELEASE=$(NGP_PROFILE_RELEASE) \
	-DNGP_DMA_ALLOW_VBLANK_TRIGGER=$(NGP_DMA_ALLOW_VBLANK_TRIGGER) \
	-DNGP_DMA_INSTALL_DONE_ISR=$(NGP_DMA_INSTALL_DONE_ISR) \
	-DNGP_DMA_INSTALL_REARM_ISR=$(NGP_DMA_INSTALL_REARM_ISR) \
	-DSFX_PLAY_EXTERNAL=1 \
	-DNGP_FAR=__far \
	-DNGP_NEAR=__near

# ---- Code modules ----
OBJS = \
    $(OBJ_DIR)/src/main.rel \
    $(OBJ_DIR)/src/game/shmup.rel \
    $(OBJ_DIR)/src/game/shmup_profile.rel \
    $(OBJ_DIR)/src/game/stage.rel \
    $(OBJ_DIR)/src/core/ngpc_sys.rel \
    $(OBJ_DIR)/src/core/ngpc_vramq.rel \
    $(OBJ_DIR)/src/core/ngpc_log.rel \
    $(OBJ_DIR)/src/core/ngpc_assert.rel \
    $(OBJ_DIR)/src/gfx/ngpc_gfx.rel \
    $(OBJ_DIR)/src/gfx/ngpc_sprite.rel \
    $(OBJ_DIR)/src/gfx/ngpc_text.rel \
    $(OBJ_DIR)/src/core/ngpc_input.rel \
    $(OBJ_DIR)/src/core/ngpc_timing.rel \
    $(OBJ_DIR)/src/core/ngpc_math.rel \
    $(OBJ_DIR)/src/core/ngpc_flash.rel \
    $(OBJ_DIR)/src/gfx/ngpc_bitmap.rel \
    $(OBJ_DIR)/src/core/ngpc_rtc.rel \
    $(OBJ_DIR)/src/gfx/ngpc_metasprite.rel \
    $(OBJ_DIR)/src/fx/ngpc_palfx.rel \
    $(OBJ_DIR)/src/fx/ngpc_raster.rel \
    $(OBJ_DIR)/src/fx/ngpc_lz.rel \
    $(OBJ_DIR)/src/fx/ngpc_lut.rel \
    $(OBJ_DIR)/src/core/ngpc_runtime.rel \
    $(OBJ_DIR)/src/core/ngpc_runtime_alias.rel

ifneq ($(strip $(NGP_ENABLE_FLASH_SAVE)),0)
OBJS += $(OBJ_DIR)/src/core/ngpc_flash_asm.rel
endif

ifneq ($(strip $(NGP_ENABLE_PROFILER)),0)
OBJS += $(OBJ_DIR)/src/fx/ngpc_debug.rel
endif

ifneq ($(strip $(NGP_ENABLE_SPRMUX)),0)
OBJS += $(OBJ_DIR)/src/fx/ngpc_sprmux.rel
endif

ifneq ($(strip $(NGP_ENABLE_DMA)),0)
OBJS += $(OBJ_DIR)/src/fx/ngpc_dma.rel
OBJS += $(OBJ_DIR)/src/fx/ngpc_dma_prog.rel
OBJS += $(OBJ_DIR)/src/fx/ngpc_dma_raster.rel
endif

# ---- Sound driver/data ----
ifneq ($(strip $(NGP_ENABLE_SOUND)),0)
OBJS += $(OBJ_DIR)/src/audio/sounds.rel
OBJS += $(OBJ_DIR)/src/audio/sounds_game_sfx.rel
OBJS += $(OBJ_DIR)/sound/sound_data.rel
endif

# ---- Graphics data (tiles, sprites, palettes) ----
OBJS += $(OBJ_DIR)/GraphX/gfx_data.rel
OBJS += $(OBJ_DIR)/GraphX/intro_ngpc_craft_png.rel
OBJS += $(OBJ_DIR)/GraphX/shmup_bg.rel
OBJS += $(OBJ_DIR)/GraphX/shmup_hudbar.rel
OBJS += $(OBJ_DIR)/GraphX/shmup_player_a_mspr.rel
OBJS += $(OBJ_DIR)/GraphX/shmup_player_b_mspr.rel
OBJS += $(OBJ_DIR)/GraphX/shmup_enemy1_mspr.rel
OBJS += $(OBJ_DIR)/GraphX/shmup_enemy2_mspr.rel
OBJS += $(OBJ_DIR)/GraphX/shmup_enemy3_mspr.rel
OBJS += $(OBJ_DIR)/GraphX/shmup_enemy5_fat_mspr.rel
OBJS += $(OBJ_DIR)/GraphX/shmup_boss1_mspr.rel
OBJS += $(OBJ_DIR)/GraphX/shmup_boss2_mspr.rel
OBJS += $(OBJ_DIR)/GraphX/shmup_bullet_mspr.rel
OBJS += $(OBJ_DIR)/GraphX/shmup_bullet2_mspr.rel
OBJS += $(OBJ_DIR)/GraphX/shmup_option_a_mspr.rel
OBJS += $(OBJ_DIR)/GraphX/shmup_option_b_mspr.rel
OBJS += $(OBJ_DIR)/GraphX/shmup_explosion_mspr.rel
OBJS += $(OBJ_DIR)/GraphX/shmup_trail_mspr.rel
OBJS += $(OBJ_DIR)/GraphX/ui_start_mspr.rel
OBJS += $(OBJ_DIR)/GraphX/ui_game_over_mspr.rel
OBJS += $(OBJ_DIR)/GraphX/ui_digits_mspr.rel
OBJS += $(OBJ_DIR)/GraphX/ui_lifebar_mspr.rel
OBJS += $(OBJ_DIR)/GraphX/ui_powerup_mspr.rel
OBJS += $(OBJ_DIR)/GraphX/shmup_drop_final_mspr.rel
OBJS += $(OBJ_DIR)/GraphX/shmup_ast1_mspr.rel
OBJS += $(OBJ_DIR)/GraphX/shmup_ast2_mspr.rel
OBJS += $(OBJ_DIR)/GraphX/shmup_ast3_mspr.rel
OBJS += $(OBJ_DIR)/GraphX/shmup_ast4_mspr.rel
OBJS += $(OBJ_DIR)/GraphX/shmup_ast5_mspr.rel
OBJS += $(OBJ_DIR)/GraphX/menu_principal.rel
OBJS += $(OBJ_DIR)/GraphX/menu_phase2.rel

TARGET_ABS = $(NAME).abs
TARGET_S24 = $(NAME).s24
TARGET_NGP = $(NAME).ngp

# system.lib — required for CLR_FLASH_RAM (block 33 erase, BIOS bug workaround).
# SysLib.txt: VECT_FLASHERS cannot erase F16_B32/B33/B34 on 16Mbit carts.
# Override path if your system.lib is elsewhere:
#   make SYSTEM_LIB=C:/path/to/system.lib
# Empty by default: this example links its own ngpc_runtime.c, so no proprietary lib is needed.
SYSTEM_LIB ?=

ifneq ($(strip $(SYSTEM_LIB)),)
LINK_LIBS = "$(SYSTEM_LIB)"
else
LINK_LIBS =
endif

# ---- Rules ----

.PHONY: all clean move_files

all: $(TARGET_NGP)

$(OBJ_DIR)/%.rel: %.c
	$(PYTHON) tools/build_utils.py compile $< $@ $(CC900_CPU) $(CDEFS)

$(OBJ_DIR)/%.rel: %.asm
	$(PYTHON) -c "import os; os.makedirs(r'$(dir $@)', exist_ok=True)"
	asm900 -g $< -o $@

$(TARGET_NGP): makefile ngpc.lcf $(OBJS)
	$(PYTHON) tools/build_utils.py link $(TARGET_ABS) ngpc.lcf $(OBJS) $(LINK_LIBS)
	$(PYTHON) tools/build_utils.py s242ngp $(TARGET_S24)
	$(PYTHON) -c "import os, shutil; os.makedirs('$(OUTPUT_DIR)', exist_ok=True); shutil.copy2('$(TARGET_NGP)', os.path.join('$(OUTPUT_DIR)', '$(NAME).ngp')); shutil.copy2('$(TARGET_NGP)', os.path.join('$(OUTPUT_DIR)', '$(NAME).ngc'))"

clean:
	$(PYTHON) tools/build_utils.py clean

move_files:
	$(PYTHON) tools/build_utils.py move $(NAME) $(TMP_DIR)
