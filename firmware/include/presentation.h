#pragma once
#include "screen_state.h"

namespace madyar {
struct AnimationSettings {
 bool enabled=true;
 uint16_t seconds=3,interval_seconds=60;
};
inline bool validAnimation(const AnimationSettings& value){
 return value.seconds>=1 && value.seconds<=10 && value.interval_seconds>=15 && value.interval_seconds<=3600;
}
inline bool animationVisible(Mode mode,uint32_t elapsed,const AnimationSettings& value){
 if(!value.enabled || !validAnimation(value))return false;
 if(mode==Mode::DONE)return true; // ScreenState already limits DONE duration.
 if(mode!=Mode::WORKING && mode!=Mode::WARNING)return false;
 return elapsed%(uint32_t(value.interval_seconds)*1000)<uint32_t(value.seconds)*1000;
}
}
