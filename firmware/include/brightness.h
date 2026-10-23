#pragma once
#include <stdint.h>
namespace madyar {
struct BrightnessSettings {
 int day_level;
 bool night_enabled;
 int start_hour, end_hour, night_level;
};
inline bool validBrightness(const BrightnessSettings& s){
 return s.day_level>=-10 && s.day_level<=100 && s.night_level>=-10 && s.night_level<=100 &&
        s.start_hour>=0 && s.start_hour<=23 && s.end_hour>=0 && s.end_hour<=23;
}
inline int brightnessLevel(const BrightnessSettings& s,uint32_t utc,int32_t utcOffset=0){
 if(!validBrightness(s))return 0;
 if(!s.night_enabled)return s.day_level;
 if(!utc)return s.day_level<s.night_level?s.day_level:s.night_level;
 const int hour=int(((int64_t(utc%86400u)+utcOffset+86400)%86400)/3600);
 const bool night=s.start_hour<s.end_hour ? hour>=s.start_hour && hour<s.end_hour :
                  s.start_hour>s.end_hour && (hour>=s.start_hour || hour<s.end_hour);
 return night?s.night_level:s.day_level;
}
inline int brightnessPwm(const BrightnessSettings& s,uint32_t utc,int32_t utcOffset=0){
 const int level=brightnessLevel(s,utc,utcOffset);
 return level<=0?1023:1023-(level*1023+50)/100;
}
}
