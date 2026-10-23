#pragma once
#include "protocol.h"
namespace madyar {
inline void expireFreshData(Snapshot& data,uint32_t now,uint32_t received,uint32_t epoch) {
 if(data.weather[0] && (!data.weather_ttl_ms || uint32_t(now-received)>=data.weather_ttl_ms)){
  data.weather[0]=data.temperature[0]=data.wind[0]=data.feels[0]=data.weather_kind[0]=0;
  data.weather_ttl_ms=0;
 }
 if(data.quota_remaining>=0 && (!data.quota_ttl_ms || uint32_t(now-received)>=data.quota_ttl_ms || !epoch || data.quota_resets<=epoch)){
  data.quota_remaining=-1;data.quota_ttl_ms=0;
 }
}
}
