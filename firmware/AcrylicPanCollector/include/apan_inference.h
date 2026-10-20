#ifndef APAN_INFERENCE_H
#define APAN_INFERENCE_H

#include <stdbool.h>
#include <stdint.h>

#include "apan_capture.h"

#define APAN_INFERENCE_OUTPUT_COUNT (12U)

void ApanInferenceInitialize(void);
bool ApanInferencePredict(const ApanEvent *event,
                          float output[APAN_INFERENCE_OUTPUT_COUNT],
                          uint8_t *class_id);
/* On-site calibration (apan_calibration.h): start from the factory beta,
   then learn the most recent ApanInferencePredict input as the target area. */
bool ApanInferenceCalibrationBegin(void);
bool ApanInferenceTrainLast(uint8_t target);
/* Run golden input `case_index` (0-11) of the 12-area model through the AxlCORE with the current beta. */
bool ApanInferenceSelfTest(uint8_t case_index, float output[APAN_INFERENCE_OUTPUT_COUNT], uint8_t *class_id);
/* Keep the working beta of the calibration as the saved one (engine mode reads it from the AxlCORE). */
bool ApanInferenceCalibrationCommit(void);
/* Load the calibration prior P0 into the AxlCORE's P matrix. */
void ApanInferenceLoadEnginePrior(void);

#endif
