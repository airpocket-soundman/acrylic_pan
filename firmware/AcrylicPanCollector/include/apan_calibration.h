#ifndef APAN_CALIBRATION_H
#define APAN_CALIBRATION_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "apan_calibration_prior.h"

/* Which beta the 12-area model uses. */
#define APAN_CALIBRATION_SOURCE_FACTORY (0U)
#define APAN_CALIBRATION_SOURCE_SAVED   (1U)
#define APAN_CALIBRATION_SOURCE_WORKING (2U)

/* The board FRAM (MB85RS2MTA, 256 KB) holds float32 P and beta; the vendor
   firmware does not use this range. */
#define APAN_CALIBRATION_FRAM_BASE (100000UL)

typedef struct
{
    uint8_t source;
    bool saved_valid;
    uint16_t saved_count;
    uint16_t work_count;
} ApanCalibrationStatus;

/* Starts the FRAM and loads the saved calibration when it belongs to
   factory_beta.  Returns false when the P0 prior does not match factory_beta;
   calibration then stays disabled and the factory beta is used. */
bool ApanCalibrationInitialize(const int16_t *factory_beta_bf16);
void ApanCalibrationGetStatus(ApanCalibrationStatus *status);

/* Active calibrated beta row as bfloat16 bits.  Returns false when the factory
   beta is active and the caller should use its own row. */
bool ApanCalibrationActiveBetaRow(uint16_t row,
                                  int16_t beta_bf16[APAN_CALIBRATION_OUTPUT_SIZE]);

/* Solist-AI hidden layer on the CPU: hard sigmoid of input . alpha, rounded
   to bfloat16 like the accelerator. */
void ApanCalibrationHidden(const int16_t *input_bf16, const int16_t *alpha_bf16,
                           uint16_t input_size,
                           float hidden[APAN_CALIBRATION_HIDDEN_SIZE]);

/* Working calibration: factory beta and P0, then one OS-ELM step per hit. */
bool ApanCalibrationBegin(void);
bool ApanCalibrationUpdate(const float hidden[APAN_CALIBRATION_HIDDEN_SIZE], uint8_t target);
bool ApanCalibrationCommit(void);
void ApanCalibrationDiscard(void);
void ApanCalibrationFactoryReset(void);

uint32_t ApanCalibrationCrc32(uint32_t crc, const void *data, size_t size);

#endif
