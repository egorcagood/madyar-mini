#pragma once
#include "screen_state.h"

namespace madyar {
// RAM only: ownership changes cancel presentation but preserve recent IDs.
class ReplyState {
 uint32_t recent_[8]{},started_=0,ttl_=0;
 uint8_t next_=0;
 bool active_=false;
public:
 bool accept(uint32_t id,uint32_t ttl,uint32_t now){
  tick(now);
  if(!id){clearPresentation();return false;}
  if(!ttl || ttl>5000)return false;
  for(auto seen:recent_)if(seen==id)return false;
  recent_[next_]=id;next_=(next_+1)%8;
  started_=now;ttl_=ttl;active_=true;return true;
 }
 void tick(uint32_t now){if(active_ && uint32_t(now-started_)>=ttl_)active_=false;}
 bool visible(uint32_t now,Mode mode,bool diagnostic){
  tick(now);return active_ && !diagnostic && (mode==Mode::HOME || mode==Mode::WORKING);
 }
 void clearPresentation(){active_=false;}
};
}
