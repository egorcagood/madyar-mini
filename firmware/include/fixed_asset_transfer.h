#pragma once
#include <stddef.h>
namespace madyar {
class FixedAssetTransfer {
 size_t expected_,received_=0;
 unsigned parts_=0;
 bool valid_=true,active_=false,ready_=false;
public:
 explicit FixedAssetTransfer(size_t expected):expected_(expected){}
 void reset(){received_=0;parts_=0;valid_=true;active_=ready_=false;}
 bool begin(){++parts_;if(parts_!=1){valid_=false;ready_=false;return false;}active_=true;return valid_;}
 bool accept(size_t bytes){
  if(!active_ || !valid_ || bytes>expected_-received_){valid_=false;ready_=false;return false;}
  received_+=bytes;return true;
 }
 void finish(bool checksum){ready_=active_ && valid_ && received_==expected_ && checksum;valid_=ready_;active_=false;}
 void abort(){valid_=active_=ready_=false;}
 bool ready()const{return ready_ && valid_ && parts_==1;}
};
}
