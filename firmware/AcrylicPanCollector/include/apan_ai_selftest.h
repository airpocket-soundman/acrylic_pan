#ifndef APAN_AI_SELFTEST_H
#define APAN_AI_SELFTEST_H

#include <stdbool.h>
#include <stdint.h>

#define APAN_AI_OUTPUT_COUNT (8U)

void ApanAiSelfTestInitialize(void);
uint8_t ApanAiSelfTestCaseCount(void);
bool ApanAiSelfTestRun(uint8_t case_id, float output[APAN_AI_OUTPUT_COUNT],
                       uint8_t *class_id);

#endif
