#ifndef APAN_INFERENCE_H
#define APAN_INFERENCE_H

#include <stdbool.h>
#include <stdint.h>

#include "apan_capture.h"

#define APAN_INFERENCE_OUTPUT_COUNT (8U)

void ApanInferenceInitialize(void);
bool ApanInferencePredict(const ApanEvent *event,
                          float output[APAN_INFERENCE_OUTPUT_COUNT],
                          uint8_t *class_id);

#endif
