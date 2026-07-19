#ifndef APAN_XY_INFERENCE_H
#define APAN_XY_INFERENCE_H

#include <stdbool.h>
#include <stdint.h>

void ApanXyInferenceInitialize(void);
bool ApanXySelfTestRun(uint8_t case_id, float output_xy[2]);

#endif
