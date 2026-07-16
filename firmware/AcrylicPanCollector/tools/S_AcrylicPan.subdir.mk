################################################################################
# Acrylic Pan collector overlay (used by LEXIDE generated make build)
################################################################################
C_SRCS += ../S_AcrylicPan/apan_capture.c ../S_AcrylicPan/apan_protocol.c ../S_AcrylicPan/apan_ai_selftest.c ../S_AcrylicPan/apan_collector_app.c
RESS += ./S_AcrylicPan/apan_capture.res ./S_AcrylicPan/apan_protocol.res ./S_AcrylicPan/apan_ai_selftest.res ./S_AcrylicPan/apan_collector_app.res
RESS__QUOTED += "./S_AcrylicPan/apan_capture.res" "./S_AcrylicPan/apan_protocol.res" "./S_AcrylicPan/apan_ai_selftest.res" "./S_AcrylicPan/apan_collector_app.res"
ASMS += ./S_AcrylicPan/apan_capture.asm ./S_AcrylicPan/apan_protocol.asm ./S_AcrylicPan/apan_ai_selftest.asm ./S_AcrylicPan/apan_collector_app.asm
ASMS__QUOTED += "./S_AcrylicPan/apan_capture.asm" "./S_AcrylicPan/apan_protocol.asm" "./S_AcrylicPan/apan_ai_selftest.asm" "./S_AcrylicPan/apan_collector_app.asm"
OBJS += ./S_AcrylicPan/apan_capture.o ./S_AcrylicPan/apan_protocol.o ./S_AcrylicPan/apan_ai_selftest.o ./S_AcrylicPan/apan_collector_app.o
OBJS__QUOTED += "./S_AcrylicPan/apan_capture.o" "./S_AcrylicPan/apan_protocol.o" "./S_AcrylicPan/apan_ai_selftest.o" "./S_AcrylicPan/apan_collector_app.o"

S_AcrylicPan/%.asm: ../S_AcrylicPan/%.c
	lccarm @"./S_AcrylicPan/$*.res"

S_AcrylicPan/%.res: S_AcrylicPan/%.asm
S_AcrylicPan/%.i: S_AcrylicPan/%.asm

S_AcrylicPan/%.o: ./S_AcrylicPan/%.asm
	llvm-mc-arm -g -dwarf-version=4 -filetype=obj -o="$@" -mcpu=cortex-m0plus "$<"
