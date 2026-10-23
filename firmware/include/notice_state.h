#pragma once
#include "protocol.h"
namespace madyar {
class NoticeState {
 Notice current_;
 uint32_t started_=0,recent_[8]{};
 uint8_t next_=0;
 bool active_=false;
public:
 bool accept(const Notice& notice,uint32_t now){
  tick(now);
  // Called only after owner/sequence acceptance. A zero ID is the parser's
  // representation of an omitted notice: cancel text, retain recent IDs.
  if(!notice.id){clearPresentation();return false;}
  if(!notice.ttl_ms || notice.ttl_ms>15000)return false;
  for(auto id:recent_)if(id==notice.id)return false;
  recent_[next_]=notice.id;next_=(next_+1)%8;
  current_=notice;started_=now;active_=true;return true;
 }
 void tick(uint32_t now){if(active_ && uint32_t(now-started_)>=current_.ttl_ms)active_=false;}
 bool visible(uint32_t now,Mode mode,bool diagnostic){
  tick(now);return active_ && !diagnostic && mode!=Mode::WARNING && mode!=Mode::DIAGNOSTIC;
 }
 void clearPresentation(){active_=false;}
 const Notice& notice()const{return current_;}
 uint32_t elapsed(uint32_t now)const{return uint32_t(now-started_);}
};
}
