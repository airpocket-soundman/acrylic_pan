#ifndef APAN_COLLECTOR_APP_H
#define APAN_COLLECTOR_APP_H

#include <stdbool.h>

void ApanCollectorAppInitialize(void);
void ApanCollectorAppSetUiStatus(bool lcd_ready);
void ApanCollectorAppProcess(void);

#endif
