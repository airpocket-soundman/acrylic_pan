/* Host-test stand-in for the vendor FRAM driver (tests/test_calibration.c). */
#ifndef FRAM_H__
#define FRAM_H__
#include <stdint.h>
void FramInit(void);
void FramReadBlock(uint32_t address, void *dst, int size);
void FramWriteBlock(uint32_t address, const void *src, int size);
#endif
