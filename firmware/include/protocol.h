#pragma once
#include <ArduinoJson.h>
#include <string.h>
#include "screen_state.h"
#include "brightness.h"
#include "display_settings.h"
#include "presentation.h"
#include "clock_style.h"

namespace madyar {
inline bool validText(const char* s,size_t maxChars,size_t maxBytes=96) {
  if(!s) return false;
  size_t count=0,bytes=0;
  const uint8_t* p=reinterpret_cast<const uint8_t*>(s);
  while(*p) {
    uint32_t c=*p++; ++bytes;
    if(c<32 || c==127 || ++count>maxChars || bytes>maxBytes) return false;
    if(c<128) continue;
    int more=0; uint32_t min=0;
    if(c>=0xc2 && c<=0xdf){ more=1; min=0x80; c&=31; }
    else if(c>=0xe0 && c<=0xef){more=2;min=0x800;c&=15;}
    else if(c>=0xf0 && c<=0xf4){more=3;min=0x10000;c&=7;}
    else return false;
    for(int i=0;i<more;++i){uint8_t d=*p++;if((d&0xc0)!=0x80)return false;c=(c<<6)|(d&63);if(++bytes>maxBytes)return false;}
    if(c<min || c>0x10ffff || (c>=0xd800 && c<=0xdfff) || (c>=0x80 && c<=0x9f))return false;
  }
  return true;
}
struct Notice {uint32_t id=0,ttl_ms=0;char title[97]{},body[289]{};};
struct Palette {
  uint32_t hour_from=0xedfbff,hour_to=0x168dbf,background_from=0x020911,background_to=0x07304a,frame=0x105070;
};
struct Snapshot {
  ClockStyle clock_style=ClockStyle::CLASSIC;
  AnimationSettings animation;
  Palette palette;
  bool show_ip=true;
  char ip_text[46]{};
  Packet packet;
  bool source_connected=false;
  uint32_t clock=0, quota_ttl_ms=0, quota_resets=0,weather_ttl_ms=0;
  int quota_remaining=-1;
  uint32_t quota_window_minutes=0;
  char label[49]{}, weather[97]{}, temperature[17]{}, wind[25]{}, feels[25]{};
  char weather_kind[12]{};
  char city[97]{},country[3]{};
  int32_t utc_offset=0;
  BrightnessSettings display=displaySettings;
  bool has_city=false,has_country=false,has_utc_offset=false,has_display=false;
  Notice notice;
  uint32_t reply_id=0,reply_ttl_ms=0;
};
inline bool uintField(JsonVariantConst v,uint32_t& out) {
  if(!v.is<uint32_t>() || v.is<bool>())return false;
  out=v.as<uint32_t>();return true;
}
inline bool copyText(JsonVariantConst v,char* out,size_t n,size_t chars) {
  if(v.isNull()){out[0]=0;return true;}
  if(!v.is<const char*>())return false;
  const char* s=v.as<const char*>();
  if(strlen(s)!=v.as<JsonString>().size() || strlen(s)>=n || !validText(s,chars,n-1))return false;
  strcpy(out,s);return true;
}
inline bool parseColor(JsonVariantConst v,uint32_t& color) {
  if(!v.is<const char*>() || v.as<JsonString>().size()!=7)return false;
  const char* p=v.as<const char*>();if(strlen(p)!=7 || *p++!='#')return false;
  color=0;
  for(int i=0;i<6;++i){unsigned char c=*p++;int n=c>='0'&&c<='9'?c-'0':c>='a'&&c<='f'?c-'a'+10:c>='A'&&c<='F'?c-'A'+10:-1;
    if(n<0)return false;
    color=(color<<4)|n;}
  return true;
}
inline bool parseSnapshot(const char* data,size_t len,Snapshot& result) {
  if(!data || !len || len>2048)return false;
  static StaticJsonDocument<3072> doc;
  doc.clear(); // one event loop; keep the parser off the ESP8266 4K stack
  if(deserializeJson(doc,data,len) || !doc.is<JsonObject>())return false;
  Snapshot s;
  if(doc.containsKey("clock_style")){
    if(!doc["clock_style"].is<const char*>())return false;
    const char* value=doc["clock_style"];
    if(strlen(value)!=doc["clock_style"].as<JsonString>().size())return false;
    if(!strcmp(value,"classic"))s.clock_style=ClockStyle::CLASSIC;
    else if(!strcmp(value,"rounded"))s.clock_style=ClockStyle::ROUNDED;
    else if(!strcmp(value,"type"))s.clock_style=ClockStyle::TYPE;
    else return false;
  }
  if(doc.containsKey("animation")){
    auto a=doc["animation"];
    if(!a.is<JsonObjectConst>() || a.as<JsonObjectConst>().size()!=3 || !a["enabled"].is<bool>() ||
       !a["seconds"].is<unsigned>() || a["seconds"].is<bool>() ||
       !a["interval_seconds"].is<unsigned>() || a["interval_seconds"].is<bool>())return false;
    const unsigned seconds=a["seconds"],interval=a["interval_seconds"];
    if(seconds<1 || seconds>10 || interval<15 || interval>3600)return false;
    s.animation={a["enabled"].as<bool>(),uint16_t(seconds),uint16_t(interval)};
  }
  if(doc.containsKey("show_ip")){if(!doc["show_ip"].is<bool>())return false;s.show_ip=doc["show_ip"].as<bool>();}
  if(doc.containsKey("ip_text")){
    if(!doc["ip_text"].is<const char*>() || !copyText(doc["ip_text"],s.ip_text,sizeof(s.ip_text),15))return false;
    const uint8_t* p=reinterpret_cast<const uint8_t*>(s.ip_text);
    while(*p){
      if(*p<128){const char c=*p++;if(!((c>='A'&&c<='Z')||(c>='a'&&c<='z')||(c>='0'&&c<='9')||strchr(" ._:-",c)))return false;}
      else {const unsigned a=*p++,b=*p++;const unsigned cp=((a&31)<<6)|(b&63);if(!((a==0xd0||a==0xd1)&&((cp>=0x410&&cp<=0x44f)||cp==0x401||cp==0x451)))return false;}
    }
    const size_t n=strlen(s.ip_text);if(n && (s.ip_text[0]==' ' || s.ip_text[n-1]==' '))return false;
  }
  if(doc.containsKey("palette")){
    JsonVariantConst v=doc["palette"];if(!v.is<JsonObjectConst>())return false;
    const size_t n=v.as<JsonObjectConst>().size();
    if(n!=4 && n!=5)return false;
    if(n==5 && !v.as<JsonObjectConst>().containsKey("frame"))return false;
    if(v.as<JsonObjectConst>().containsKey("frame") && !parseColor(v["frame"],s.palette.frame))return false;
    if(!parseColor(v["hour_from"],s.palette.hour_from) || !parseColor(v["hour_to"],s.palette.hour_to) ||
       !parseColor(v["background_from"],s.palette.background_from) || !parseColor(v["background_to"],s.palette.background_to))return false;
  }
  const char* mode=doc["mode"].as<const char*>();
  if(!mode || strlen(mode)!=doc["mode"].as<JsonString>().size())return false;
  if(!strcmp(mode,"HOME"))s.packet.mode=Mode::HOME;
  else if(!strcmp(mode,"WORKING"))s.packet.mode=Mode::WORKING;
  else if(!strcmp(mode,"WARNING"))s.packet.mode=Mode::WARNING;
  else if(!strcmp(mode,"DONE"))s.packet.mode=Mode::DONE;
  else if(!strcmp(mode,"DIAGNOSTIC"))s.packet.mode=Mode::DIAGNOSTIC;
  else return false;
  uint32_t active,waiting;
  if(!uintField(doc["boot"],s.packet.boot) || !uintField(doc["owner"],s.packet.owner) ||
     !uintField(doc["seq"],s.packet.seq) || !uintField(doc["active"],active) ||
     !uintField(doc["waiting"],waiting) || !uintField(doc["lease_ms"],s.packet.lease_ms) ||
     !uintField(doc["done_ms"],s.packet.done_ms) || !doc["source_connected"].is<bool>())return false;
  if(active>8 || waiting>8 || active+waiting>8)return false;
  s.packet.active=active;s.packet.waiting=waiting;s.source_connected=doc["source_connected"];
  if(doc.containsKey("city")){
    if(!doc["city"].is<const char*>() || !copyText(doc["city"],s.city,sizeof(s.city),24))return false;
    s.has_city=true;
  }
  if(doc.containsKey("country")){
    if(!doc["country"].is<const char*>())return false;
    const char* c=doc["country"];if(doc["country"].as<JsonString>().size()!=2 || strlen(c)!=2 || c[0]<'A' || c[0]>'Z' || c[1]<'A' || c[1]>'Z')return false;
    strcpy(s.country,c);s.has_country=true;
  }
  if(doc.containsKey("utc_offset")){
    auto v=doc["utc_offset"];if(!v.is<int32_t>() || v.is<bool>())return false;
    s.utc_offset=v.as<int32_t>();if(s.utc_offset< -43200 || s.utc_offset>50400)return false;s.has_utc_offset=true;
  }
  if(doc.containsKey("display")){
    auto d=doc["display"];if(!d.is<JsonObject>() || !d["night_enabled"].is<bool>())return false;
    const char* keys[]={"brightness","night_start","night_end","night_brightness"};
    int values[4];for(int i=0;i<4;++i){if(!d[keys[i]].is<int>() || d[keys[i]].is<bool>())return false;values[i]=d[keys[i]].as<int>();}
    s.display={values[0],d["night_enabled"].as<bool>(),values[1],values[2],values[3]};
    if(!validBrightness(s.display) || values[0]<0 || values[3]<0)return false;
    s.has_display=true;
  }
  if(doc.containsKey("reply_ready")){
    JsonVariantConst r=doc["reply_ready"];
    if(!r.is<JsonObjectConst>() || r.as<JsonObjectConst>().size()!=2 ||
       !uintField(r["id"],s.reply_id) || !s.reply_id ||
       !uintField(r["ttl_ms"],s.reply_ttl_ms) || !s.reply_ttl_ms || s.reply_ttl_ms>5000)return false;
  }
  if(doc.containsKey("notice")){
    auto n=doc["notice"];
    if(!n.is<JsonObject>() || !uintField(n["id"],s.notice.id) || !s.notice.id ||
       !uintField(n["ttl_ms"],s.notice.ttl_ms) || !s.notice.ttl_ms || s.notice.ttl_ms>15000 ||
       !n["app"].is<const char*>() || n["app"].as<JsonString>().size()!=8 || strcmp(n["app"].as<const char*>(),"telegram") ||
       !n["title"].is<const char*>() || !n["body"].is<const char*>() ||
       !copyText(n["title"],s.notice.title,sizeof(s.notice.title),24) ||
       !copyText(n["body"],s.notice.body,sizeof(s.notice.body),96))return false;
  }
  if(!s.source_connected && s.packet.mode!=Mode::HOME && s.packet.mode!=Mode::DIAGNOSTIC)return false;
  if((s.packet.mode==Mode::DONE || s.packet.mode==Mode::HOME) && (active||waiting))return false;
  if(s.packet.mode==Mode::WORKING && (!active||waiting))return false;
  if(s.packet.mode==Mode::WARNING && !waiting)return false;
  if(!doc["clock"].isNull()) {
    if(!uintField(doc["clock"],s.clock) || s.clock<1577836800u || s.clock>4102444800u)return false;
  }
  if(!doc["quota"].isNull()) {
    auto q=doc["quota"];
    if(q.containsKey("window_minutes") && (!uintField(q["window_minutes"],s.quota_window_minutes) || !s.quota_window_minutes || s.quota_window_minutes>525600))return false;
    if(!q["remaining"].is<int>() || q["remaining"].is<bool>())return false;
    s.quota_remaining=q["remaining"].as<int>();
    if(s.quota_remaining<0 || s.quota_remaining>100 ||
       !uintField(q["ttl_ms"],s.quota_ttl_ms) || s.quota_ttl_ms>600000 ||
       !uintField(q["resets_at"],s.quota_resets))return false;
  }
  if(!copyText(doc["label"],s.label,sizeof(s.label),12) ||
     !copyText(doc["weather"]["description"],s.weather,sizeof(s.weather),40) ||
     !copyText(doc["weather"]["temperature"],s.temperature,sizeof(s.temperature),12) ||
     !copyText(doc["weather"]["kind"],s.weather_kind,sizeof(s.weather_kind),10) ||
     !copyText(doc["weather"]["wind"],s.wind,sizeof(s.wind),16) ||
     !copyText(doc["weather"]["feels"],s.feels,sizeof(s.feels),16))return false;
  if(s.weather[0] && (!uintField(doc["weather"]["ttl_ms"],s.weather_ttl_ms) || s.weather_ttl_ms>3600000))return false;
  result=s;return true;
}
}
