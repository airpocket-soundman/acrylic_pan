/*
 * Replacement for S_System/main.c in a private copy of AIVibrationInference.
 * Vendor source is intentionally not vendored in this repository.
 */
#include <stdint.h>

#include "ConfigData.h"
#include "Lcd.h"
#include "PeriodicHandler10ms.h"
#include "Regulator5VOutput.h"
#include "Sleep.h"
#include "SoftwareInterrupt.h"
#include "SystemError.h"
#include "SystemPowerControl.h"
#include "TimeControl.h"
#include "Uart1.h"
#include "apan_collector_app.h"
#include "clock.h"
#include "irq.h"
#include "mcu.h"
#include "smpl_common.h"
#include "smpl_common_led.h"
#include "timer0_1.h"
#include "wdt.h"

int32_t main(void)
{
    bool lcd_ready;
    __disable_irq();
    wdt_init(WDT_2S);
    wdt_clear();
    smpl_setLsCrystal32Khz();
    smpl_setHsPll48Mhz(CLK_XSPEN_DIS, CLK_HXSPEN_DIS);
    __enable_irq();

    SystemPowerControlInit();
    SoftwareInterruptInit();
    TimeControlInit();
    PeriodicHandler10msInit();
    timer0_start();
    Uart1PeripheralInit();
    SystemErrorInit();

    smpl_initLED1(LED_ACTIVE);
    smpl_initLED2(LED_ACTIVE);
    smpl_initLED3(LED_ACTIVE);
    Regulator5VOutputInit();
    Regulator5VOutputOn();
    LcdPeripheralInit();
    LcdInit();
    LcdDisplayOnOff(LCD_DISPLAY_ON, LCD_CURSOR_OFF, LCD_CURSOR_BLINK_OFF);
    LcdClearDisplay();
    lcd_ready = LcdDraw(LCD_START_OF_FIRST_LINE, "test");
    LcdBacklightOn();

    ApanCollectorAppInitialize();
    ApanCollectorAppSetUiStatus(lcd_ready);

    for (;;)
    {
        ApanCollectorAppProcess();
        wdt_clear();
        SleepChangetoHaltMode();
    }
}
