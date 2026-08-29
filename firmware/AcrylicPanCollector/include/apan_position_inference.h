#ifndef APAN_POSITION_INFERENCE_H
#define APAN_POSITION_INFERENCE_H

#include <stdbool.h>
#include <stdint.h>

#include "apan_capture.h"

#define APAN_POSITION_OUTPUT_COUNT (60U)

void ApanPositionInferenceInitialize(void);
bool ApanPositionInferencePredict(const ApanEvent *event,
                                  float logits[APAN_POSITION_OUTPUT_COUNT],
                                  uint8_t *position_id);
bool ApanPositionInferenceSelfTest(uint8_t case_id,
                                   float logits[APAN_POSITION_OUTPUT_COUNT],
                                   uint8_t *position_id);
void ApanPositionInferenceSupport(uint8_t position_id, uint16_t *x_mm,
                                  uint16_t *y_mm);

#endif
