#ifndef APAN_ODL_EXPERIMENT_H
#define APAN_ODL_EXPERIMENT_H

#include <stdbool.h>
#include <stdint.h>

/* Engine-ODL stability experiment (docs/odl-engine-experiment-20261020.md).  Off in the normal firmware: the
   training hits are 60 KB of flash that only the experiment build carries. */
#ifndef APAN_ODL_EXPERIMENT
#define APAN_ODL_EXPERIMENT 0
#endif

#if APAN_ODL_EXPERIMENT
#define APAN_ODL_EXPERIMENT_DUMP_ENGINE_PARTS (4U)  /* 8 beta rows (192 bytes) each */
#define APAN_ODL_EXPERIMENT_DUMP_CPU_PARTS    (8U)  /* 4 beta rows (192 bytes) each */

/* Factory beta and the P0 prior in both the AxlCORE and the CPU float32 OS-ELM. */
bool ApanOdlExperimentBegin(void);
/* Learn the next `count` hits in both.  `engine_cycles` is the AxlCORE time (SysTick cycles) of this call. */
bool ApanOdlExperimentRun(uint8_t count, uint16_t *updates, uint32_t *engine_cycles);
/* Copy a slice of beta (bfloat16 from the AxlCORE, float32 from the CPU); returns the byte count or 0. */
uint8_t ApanOdlExperimentDumpEngine(uint8_t part, uint8_t *out);
uint8_t ApanOdlExperimentDumpCpu(uint8_t part, uint8_t *out);
/* Live strikes: after BEGIN, the calibration ARM operation learns each struck hit in both the AxlCORE and the CPU
   (the AxlCORE keeps its own beta).  The last model input (128 bfloat16 values) can be read back. */
bool ApanOdlExperimentTrainLive(const uint16_t *input, uint8_t target);
const uint16_t *ApanInferenceLastInput(void);
void ApanInferenceSetExperimentLive(bool live);
#endif

#endif
