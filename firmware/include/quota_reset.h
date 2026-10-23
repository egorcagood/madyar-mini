#pragma once
#include <Arduino.h>
#include <stdio.h>
#include <time.h>
namespace madyar {
inline bool resetCaption(uint32_t resets,uint32_t epoch,int32_t offset,bool datePhase,char* head,char* body){
 head[0]=body[0]=0;if(!epoch || resets<=epoch)return false;
 if(datePhase){
  snprintf(head,40,"Сброс");time_t local=int64_t(resets)+offset;tm value{};gmtime_r(&local,&value);
  snprintf(body,40,"%02d.%02d",value.tm_mday,value.tm_mon+1);return true;
 }
 snprintf(head,40,"До сброса");const uint32_t left=resets-epoch,days=left/86400,hours=left%86400/3600;
 if(days)snprintf(body,40,"%u д. %u ч.",unsigned(days),unsigned(hours));
 else if(left>=3600)snprintf(body,40,"%u ч. %u м.",unsigned(left/3600),unsigned(left%3600/60));
 else snprintf(body,40,"%u мин.",unsigned((left+59)/60));
 return true;
}
}

