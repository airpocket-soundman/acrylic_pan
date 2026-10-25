#include <stdio.h>
#include <string.h>

#include "Fram.h"
#include "SoftSpi.h"
#include "wdt.h"
#include "apan_position_probability_model.h"
#include "apan_calibration.h"

#define CHECK(condition) do { if (!(condition)) { \
    fprintf(stderr, "check failed at line %d: %s\n", __LINE__, #condition); return 1; \
} } while (0)

#define FRAM_SIZE (256UL * 1024UL)
#define P_BYTES (APAN_CALIBRATION_HIDDEN_SIZE * APAN_CALIBRATION_HIDDEN_SIZE * 4UL)
#define BETA_BYTES (APAN_CALIBRATION_HIDDEN_SIZE * APAN_CALIBRATION_OUTPUT_SIZE * 4UL)
#define SAVED_HEADER (APAN_CALIBRATION_FRAM_BASE + P_BYTES + 2UL * BETA_BYTES)
static unsigned char fram[FRAM_SIZE];
static int fram_initialized;
static int16_t engine_beta[APAN_CALIBRATION_HIDDEN_SIZE][APAN_CALIBRATION_OUTPUT_SIZE];

void SoftSpiPeripheralInit(void) {}
void wdt_clear(void) {}
void FramInit(void) { fram_initialized = 1; }
void FramReadBlock(uint32_t address, void *dst, int size)
{
    memcpy(dst, &fram[address], (size_t)size);
}
void FramWriteBlock(uint32_t address, const void *src, int size)
{
    memcpy(&fram[address], src, (size_t)size);
}

static bool read_engine_row(uint16_t row, int16_t out[APAN_CALIBRATION_OUTPUT_SIZE])
{
    memcpy(out, engine_beta[row], sizeof(engine_beta[row]));
    return true;
}

int main(void)
{
    ApanCalibrationStatus status;
    float hidden[APAN_CALIBRATION_HIDDEN_SIZE];
    float soft_target[APAN_CALIBRATION_OUTPUT_SIZE] = {0.0F};
    int16_t row[APAN_CALIBRATION_OUTPUT_SIZE];
    int16_t wrong_beta[APAN_POSITION_HIDDEN_SIZE * APAN_POSITION_OUTPUT_SIZE];
    unsigned i;
    unsigned j;

    CHECK(ApanCalibrationCrc32(0UL, apan_position_beta, sizeof(apan_position_beta)) ==
          APAN_CALIBRATION_MODEL_CRC32);
    CHECK(ApanCalibrationCrc32(0UL, "123456789", 9U) == 0xCBF43926UL);
    memcpy(wrong_beta, apan_position_beta, sizeof(wrong_beta));
    wrong_beta[0] ^= 1;
    CHECK(!ApanCalibrationInitialize(wrong_beta));
    CHECK(!fram_initialized);
    CHECK(!ApanCalibrationBegin(APAN_CALIBRATION_KIND_QUICK));

    CHECK(ApanCalibrationInitialize(apan_position_beta));
    ApanCalibrationGetStatus(&status);
    CHECK(status.source == APAN_CALIBRATION_SOURCE_FACTORY);
    CHECK(status.saved_kind == APAN_CALIBRATION_KIND_NONE);
    CHECK(!ApanCalibrationActiveBetaRow(0U, row));

    for (i = 0U; i < APAN_CALIBRATION_HIDDEN_SIZE; i++) { hidden[i] = 0.25F; }
    soft_target[6] = 0.6F;
    soft_target[0] = soft_target[1] = soft_target[9] = soft_target[10] = 0.1F;
    CHECK(ApanCalibrationBegin(APAN_CALIBRATION_KIND_QUICK));
    CHECK(ApanCalibrationUpdateTarget(hidden, soft_target));
    ApanCalibrationGetStatus(&status);
    CHECK(status.work_count == 1U);
    CHECK(status.work_kind == APAN_CALIBRATION_KIND_QUICK);
    CHECK(ApanCalibrationActiveBetaRow(0U, row));
    CHECK(ApanCalibrationCommit());
    ApanCalibrationGetStatus(&status);
    CHECK(status.source == APAN_CALIBRATION_SOURCE_SAVED);
    CHECK(status.saved_count == 1U);
    CHECK(status.saved_kind == APAN_CALIBRATION_KIND_QUICK);

    CHECK(ApanCalibrationInitialize(apan_position_beta));
    ApanCalibrationGetStatus(&status);
    CHECK(status.source == APAN_CALIBRATION_SOURCE_SAVED);
    CHECK(status.saved_kind == APAN_CALIBRATION_KIND_QUICK);
    CHECK(ApanCalibrationBegin(APAN_CALIBRATION_KIND_PRECISE));
    ApanCalibrationDiscard();
    ApanCalibrationGetStatus(&status);
    CHECK(status.source == APAN_CALIBRATION_SOURCE_SAVED);
    CHECK(status.work_kind == APAN_CALIBRATION_KIND_NONE);

    /* The persisted mode is part of the saved-record validity check. */
    fram[SAVED_HEADER + 10U] = APAN_CALIBRATION_KIND_NONE;
    fram[SAVED_HEADER + 11U] = 0U;
    CHECK(ApanCalibrationInitialize(apan_position_beta));
    ApanCalibrationGetStatus(&status);
    CHECK(status.source == APAN_CALIBRATION_SOURCE_FACTORY);
    CHECK(!status.saved_valid);

    ApanCalibrationFactoryReset();
    CHECK(ApanCalibrationInitialize(apan_position_beta));
    CHECK(ApanCalibrationBeginEngine(APAN_CALIBRATION_KIND_PRECISE));
    for (i = 0U; i < APAN_CALIBRATION_HIDDEN_SIZE; i++)
    {
        for (j = 0U; j < APAN_CALIBRATION_OUTPUT_SIZE; j++)
        {
            engine_beta[i][j] = (int16_t)(0x3A00 + (int)((i + j) & 0x01FFU));
        }
    }
    CHECK(ApanCalibrationNoteTrained());
    CHECK(ApanCalibrationCommitEngine(read_engine_row));
    CHECK(ApanCalibrationInitialize(apan_position_beta));
    ApanCalibrationGetStatus(&status);
    CHECK(status.saved_kind == APAN_CALIBRATION_KIND_PRECISE);
    CHECK(ApanCalibrationActiveBetaRow(7U, row));
    CHECK(memcmp(row, engine_beta[7], sizeof(row)) == 0);

    printf("60-position calibration host test passed\n");
    return 0;
}
