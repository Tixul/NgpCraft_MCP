.SUFFIXES: .c .asm .rel .abs

NAME = main
OUTPUT_DIR = bin

CC = cc900

OBJS = \
	main.rel \
	game.rel \
	input.rel \
	controller.rel \
	ctrl_human.rel \
	ctrl_cpu.rel \
	library.rel \
	core/rom/flash.rel \
	assets.rel \
	terrain.rel \
	app.rel \
	context.rel \
	state_intro.rel \
	state_title.rel \
	state_select_player.rel \
	state_select_arena.rel \
	state_match.rel \
	state_options.rel \
	terrain_data.rel \
	personnage.rel \
	defs_data.rel \
	sounds.rel \
	sounds_game_sfx.rel \
	samples/samploop.rel


all: $(NAME).ngp

$(NAME).ngp: makefile ngpc.lcf $(OBJS)
	tulink -la -o $(NAME).abs ngpc.lcf system.lib $(OBJS)
	tuconv -Fs24 $(NAME).abs
	s242ngp $(NAME).s24

.c.rel:
	$(CC) -c -O3 $< -o $@

# If you later add .asm files, ensure your assembler is in PATH and add a rule here.

core/rom/%.rel: core/rom/%.c
	$(CC) -c -O3 $< -o $@

clean:
	@if exist $(OUTPUT_DIR)\*.abs del /q $(OUTPUT_DIR)\*.abs
	@if exist $(OUTPUT_DIR)\*.map del /q $(OUTPUT_DIR)\*.map
	@if exist $(OUTPUT_DIR)\*.s24 del /q $(OUTPUT_DIR)\*.s24
	@if exist $(OUTPUT_DIR)\*.ngc del /q $(OUTPUT_DIR)\*.ngc
	@if exist $(OUTPUT_DIR)\*.ngp del /q $(OUTPUT_DIR)\*.ngp
	@if exist *.rel del /q *.rel
	@if exist core\rom\*.rel del /q core\rom\*.rel
	@if exist $(NAME).abs del /q $(NAME).abs
	@if exist $(NAME).map del /q $(NAME).map
	@if exist $(NAME).s24 del /q $(NAME).s24
	@if exist $(NAME).ngp del /q $(NAME).ngp

move_files:
	@if not exist "$(OUTPUT_DIR)" mkdir "$(OUTPUT_DIR)"
	@if exist "$(NAME).ngp" move /y "$(NAME).ngp" "$(OUTPUT_DIR)\$(NAME).ngc" >nul
	@if exist "$(NAME).abs" move /y "$(NAME).abs" "$(OUTPUT_DIR)\$(NAME).abs" >nul
	@if exist "$(NAME).s24" move /y "$(NAME).s24" "$(OUTPUT_DIR)\$(NAME).s24" >nul
	@if exist "$(NAME).map" move /y "$(NAME).map" "$(OUTPUT_DIR)\$(NAME).map" >nul
