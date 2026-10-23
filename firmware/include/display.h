#pragma once
#include <Arduino.h>
#include <TFT_eSPI.h>
#include "protocol.h"
extern TFT_eSPI tft;
void homeBegin(bool mounted);
void homeSuspend();
void homeReplyAccept(uint32_t id,uint32_t ttl_ms,uint32_t now);
void homeReplyClear();
bool homeReplyVisible(uint32_t now,madyar::Mode mode,bool diagnostic);
void noticeDraw(const madyar::Notice& notice,uint32_t elapsed,bool force,const madyar::Palette* theme=nullptr);
void homeDraw(const madyar::Snapshot& data,madyar::ScreenState& state,
              uint32_t now,uint32_t epoch,uint32_t received,bool force);
void gifStop();
bool gifStart(madyar::Mode mode);
bool gifTick(uint32_t now);
