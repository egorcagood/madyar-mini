#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <ESP8266WebServer.h>
#include <ESP8266HTTPUpdateServer.h>
#include <LittleFS.h>
#include <bearssl/bearssl_hash.h>
extern "C" {
#include <user_interface.h>
}
#include "private_config.h"
#include "resource_manifest.h"
#include "display.h"
#include "loop_time.h"
#include "fresh_data.h"
#include "display_settings.h"
#include "notice_state.h"
#include "fixed_asset_transfer.h"

static ESP8266WebServer server(80);
static ESP8266HTTPUpdateServer updater;
static madyar::ScreenState screen(0);
static madyar::Snapshot current;
static madyar::NoticeState notices;
static madyar::BrightnessSettings runtimeDisplay=madyar::displaySettings;
static uint32_t bootId,received=0,clockEpoch=0,clockTick=0,startup=0,modeSince=0;
static bool mounted=false,assetsOkay=false,recovery=false,apStarted=false,healthy=false;
static madyar::Mode previous=madyar::Mode::DIAGNOSTIC;
static bool previousInfo=false;
static bool previousNotice=false,drawSuspended=false;
static int previousPwm=-1;
static File fontUploadFile;
static br_sha256_context fontUploadHash;
static madyar::FixedAssetTransfer fontTransfer(FONT_BYTES);
static void updateBrightness(){
 int pwm=madyar::brightnessPwm(runtimeDisplay,clockEpoch,current.utc_offset);
 if(pwm!=previousPwm){analogWrite(5,pwm);previousPwm=pwm;}
}
static uint32_t nonzeroRandom(){uint32_t n;do{n=os_random();}while(!n);return n;}
static bool authorized(){
 if(server.header("X-Madyar-Key")!=MADYAR_KEY){server.send(401,"application/json","{\"error\":\"unauthorized\"}");return false;}
 return true;
}
static bool verifyResources(){
 uint8_t block[256],digest[32];
 for(const auto& r:resources){
  File f=LittleFS.open(r.path,"r");if(!f || f.size()!=r.bytes)return false;
  br_sha256_context hash;br_sha256_init(&hash);
  while(f.available()){int n=f.read(block,sizeof(block));if(n<=0)return false;br_sha256_update(&hash,block,n);yield();}
  br_sha256_out(&hash,digest);f.close();
  static const char hex[]="0123456789abcdef";
  for(int i=0;i<32;++i)if(hex[digest[i]>>4]!=r.sha[i*2] || hex[digest[i]&15]!=r.sha[i*2+1])return false;
 }
 return true;
}
static void suspendReaders(){
 if(drawSuspended)return;
 drawSuspended=true;gifStop();homeSuspend();notices.clearPresentation();homeReplyClear();
}
static void resumeReaders(){
 // Failed filesystem uploads can leave a partial image. Never format or trust it.
 LittleFS.end();mounted=false;assetsOkay=false;
 if(!recovery){LittleFSConfig config(false);LittleFS.setConfig(config);mounted=LittleFS.begin();assetsOkay=mounted && verifyResources();}
 homeBegin(assetsOkay);drawSuspended=false;previous=madyar::Mode::DIAGNOSTIC;previousInfo=previousNotice=false;
}
static void setupRoutes(){
 // Pinned core 3.1.2 invokes this before parsing uploads. The HTTP updater
 // closes all FS instances BEFORE Update.begin(U_FS), so onStart alone is late.
 server.addHook([](const String& method,const String& path,WiFiClient*,ESP8266WebServer::ContentTypeFunction){
  if(method=="POST" && path=="/update")suspendReaders();
  if(method=="POST" && path=="/api/v1/font")fontTransfer.reset();
  return ESP8266WebServer::CLIENT_REQUEST_CAN_CONTINUE;
 });
 Update.onStart(suspendReaders);
 server.collectHeaders("X-Madyar-Key","Origin");
 server.on("/",HTTP_GET,[]{
  server.send(200,"text/plain; charset=utf-8","Madyar Mini 0.3.0-public.1\n240x240 local display\nAuthenticated update: /update\nState API: /api/v1/health\n");
 });
 server.on("/api/v1/health",HTTP_GET,[]{
  if(!authorized())return;
  StaticJsonDocument<1536> d;d["firmware"]="MadyarMini-0.3.0-public.1";d["protocol"]=1;d["integrated_api"]=1;
  d["clock_style"]=uint8_t(current.clock_style);d["font_revision"]=assetsOkay?"open-bold-14":"pending";
  d["clock_first"]=true;d["animations_enabled"]=current.animation.enabled;
  d["animation_seconds"]=current.animation.seconds;d["animation_interval_seconds"]=current.animation.interval_seconds;
  d["chip"]="ESP8266";d["flash_bytes"]=ESP.getFlashChipRealSize();d["boot"]=bootId;
  d["connected"]=screen.connected(millis());d["seq"]=screen.seq();
  d["mode"]=uint8_t(screen.mode(millis()));d["recovery"]=recovery;
  d["assets_verified"]=assetsOkay;d["fs_mounted"]=mounted;
  d["free_heap"]=ESP.getFreeHeap();d["max_free_block"]=ESP.getMaxFreeBlockSize();
  d["free_sketch_space"]=ESP.getFreeSketchSpace();d["state_flash_writes"]=0;
  d["brightness_level"]=madyar::brightnessLevel(runtimeDisplay,clockEpoch,current.utc_offset);
  d["night_enabled"]=runtimeDisplay.night_enabled;d["clock_valid"]=clockEpoch!=0;
  auto display=d.createNestedObject("display");display["brightness"]=runtimeDisplay.day_level;
  display["night_enabled"]=runtimeDisplay.night_enabled;display["night_start"]=runtimeDisplay.start_hour;
  display["night_end"]=runtimeDisplay.end_hour;display["night_brightness"]=runtimeDisplay.night_level;
  d["city"]=current.city;d["country"]=current.country;d["utc_offset"]=current.utc_offset;d["clock"]=clockEpoch;
  d["draw_suspended"]=drawSuspended;
  const uint32_t now=millis();const auto mode=screen.mode(now);const bool diagnostic=screen.diagnostic(now);
  d["reply_ready_visible"]=assetsOkay && !recovery && !drawSuspended && screen.connected(now) && current.source_connected &&
    !notices.visible(now,mode,diagnostic) && homeReplyVisible(now,mode,diagnostic);
  d["weather_valid"]=current.weather[0] && uint32_t(millis()-received)<current.weather_ttl_ms;
  d["quota_resets_at"]=current.quota_resets;
  String body;serializeJson(d,body);server.send(200,"application/json",body);
 });
 server.on("/api/v1/font",HTTP_POST,[]{
  if(!authorized())return;
  if(server.header("Origin").length()){server.send(403);return;}
  fontUploadFile.close();
  bool okay=fontTransfer.ready() && mounted && !recovery;
  if(okay)okay=LittleFS.rename("/font.next","/font.bin");
  if(!okay && mounted)LittleFS.remove("/font.next");
  if(drawSuspended)resumeReaders();
  server.send(okay && assetsOkay?200:400,"application/json",okay && assetsOkay?"{\"ok\":true,\"font_updated\":true}":"{\"error\":\"font_rejected\"}");
 },[]{
  auto& upload=server.upload();
  if(upload.status==UPLOAD_FILE_START){
   if(server.header("X-Madyar-Key")!=MADYAR_KEY || server.header("Origin").length() || !mounted || recovery || upload.name!="font" || !fontTransfer.begin()){
    fontTransfer.abort();return;
   }
   suspendReaders();fontUploadFile=LittleFS.open("/font.next","w");
   if(!fontUploadFile){fontTransfer.abort();return;}
   br_sha256_init(&fontUploadHash);
  }else if(upload.status==UPLOAD_FILE_WRITE){
   if(!fontUploadFile || !fontTransfer.accept(upload.currentSize)){fontTransfer.abort();return;}
   if(fontUploadFile.write(upload.buf,upload.currentSize)!=upload.currentSize){fontTransfer.abort();return;}
   br_sha256_update(&fontUploadHash,upload.buf,upload.currentSize);
  }else if(upload.status==UPLOAD_FILE_END){
   if(!fontUploadFile){fontTransfer.abort();return;}
   fontUploadFile.flush();fontUploadFile.close();uint8_t digest[32];br_sha256_out(&fontUploadHash,digest);
   static const char hex[]="0123456789abcdef";bool match=true;
   for(int i=0;i<32;++i)if(hex[digest[i]>>4]!=FONT_SHA256[i*2] || hex[digest[i]&15]!=FONT_SHA256[i*2+1])match=false;
   fontTransfer.finish(match);
  }else if(upload.status==UPLOAD_FILE_ABORTED){
   fontUploadFile.close();fontTransfer.abort();if(mounted)LittleFS.remove("/font.next");
  }
  yield();
 });
 server.on("/api/v1/claim",HTTP_POST,[]{
  if(!authorized())return;
  if(server.header("Origin").length()){server.send(403);return;}
  const String body=server.arg("plain");StaticJsonDocument<128>d;
  if(body.length()>128 || deserializeJson(d,body) || !d["boot"].is<uint32_t>() || d["boot"].as<uint32_t>()!=bootId){server.send(400);return;}
  uint32_t owner=nonzeroRandom();
  if(!screen.claim(owner,millis())){server.send(409,"application/json","{\"error\":\"controller_busy\"}");return;}
  notices.clearPresentation();homeReplyClear();
  d.clear();d["boot"]=bootId;d["owner"]=owner;String result;serializeJson(d,result);server.send(200,"application/json",result);
 });
 server.on("/api/v1/state",HTTP_POST,[]{
  if(!authorized())return;
  if(server.header("Origin").length()){server.send(403);return;}
  const String body=server.arg("plain");madyar::Snapshot snap;
  if(!madyar::parseSnapshot(body.c_str(),body.length(),snap)){server.send(400,"application/json","{\"error\":\"invalid_snapshot\"}");return;}
  uint32_t now=millis();
  if(!screen.accept(snap.packet,now)){server.send(409,"application/json","{\"error\":\"stale_or_invalid_owner\"}");return;}
  if(!snap.has_city)strcpy(snap.city,current.city);
  if(!snap.has_country)strcpy(snap.country,current.country);
  if(!snap.has_utc_offset)snap.utc_offset=current.utc_offset;
  if(snap.has_display)runtimeDisplay=snap.display;
  snap.display=runtimeDisplay;
  // Optional palette/show_ip intentionally use protocol defaults on omission for legacy senders.
  // Each accepted snapshot owns notice presentation. Omission cancels it;
  // rejected owner/sequence packets above cannot change or clear a notice.
  notices.accept(snap.notice,now);
  homeReplyAccept(snap.reply_id,snap.reply_ttl_ms,now);
  if(!snap.source_connected)homeReplyClear();
  current=snap;received=now;
  if(snap.clock){clockEpoch=snap.clock;clockTick=now;}
  server.send(200,"application/json","{\"ok\":true}");
 });
 server.on("/api/v1/wifi",HTTP_POST,[]{
  if(!authorized())return;
  if(server.header("Origin").length()){server.send(403);return;}
  const String body=server.arg("plain");StaticJsonDocument<384>d;
  if(body.length()>256 || deserializeJson(d,body) || !d["ssid"].is<const char*>() || !d["password"].is<const char*>()){server.send(400);return;}
  String ssid=d["ssid"].as<const char*>(),password=d["password"].as<const char*>();
  if(ssid.length()<1 || ssid.length()>32 || password.length()<8 || password.length()>63){server.send(400);return;}
  server.send(200,"application/json","{\"ok\":true,\"operation\":\"explicit_wifi_configuration\"}");
  WiFi.persistent(true);WiFi.begin(ssid.c_str(),password.c_str());WiFi.persistent(false);
 });
 updater.setup(&server,"/update","madyar",MADYAR_KEY);
 server.onNotFound([]{server.send(404);});server.begin();
}
static void startFallback(){
 if(apStarted)return;
 String name="MadyarMini-"+String(ESP.getChipId(),HEX);
 // SDK configuration is never persisted by ordinary state/connection updates.
 WiFi.mode(WIFI_AP_STA);WiFi.softAP(name.c_str(),String(MADYAR_KEY).substring(0,12).c_str());apStarted=true;
 // homeDraw owns the entire screen. Clearing it here leaves unpainted bands
 // between its widgets and interrupts the M animation when setup starts.
}
void setup(){
 Serial.begin(115200);startup=millis();bootId=nonzeroRandom();screen.reset(bootId);
 struct BootRecord {uint32_t magic,count;} record{};
 if(!ESP.rtcUserMemoryRead(0,reinterpret_cast<uint32_t*>(&record),sizeof(record)) || record.magic!=0x4d4d5631)record={0x4d4d5631,0};
 record.count=record.count<10?record.count+1:10;
 ESP.rtcUserMemoryWrite(0,reinterpret_cast<uint32_t*>(&record),sizeof(record));
 recovery=record.count>=3 || ESP.getFlashChipRealSize()!=4194304;
 tft.init();tft.setRotation(0);tft.setSwapBytes(true);
 pinMode(5,OUTPUT);analogWriteRange(1023);updateBrightness(); // active-low, captured settings
 tft.fillScreen(TFT_BLACK);tft.setTextColor(TFT_CYAN,TFT_BLACK);tft.drawString("Madyar Mini",40,100,2);
 WiFi.persistent(false);WiFi.mode(WIFI_STA);WiFi.hostname("madyar-mini");WiFi.begin();
 setupRoutes();
 if(!recovery){LittleFSConfig config(false);LittleFS.setConfig(config);mounted=LittleFS.begin();
  assetsOkay=mounted && verifyResources();
 }
 homeBegin(assetsOkay);
}
void loop(){
 uint32_t now=madyar::serviceAndSample([]{server.handleClient();},[]{return millis();});
 // handleClient parses each multipart upload synchronously, including abort.
 // A failed write may keep Updater allocated; end it before reopening files.
 if(drawSuspended){
  if(Update.hasError() && Update.isRunning())Update.end();
  if(!Update.isRunning())resumeReaders();
  else {yield();delay(1);return;}
 }
 screen.tick(now);
 notices.tick(now);if(!screen.connected(now)){notices.clearPresentation();homeReplyClear();}
 if(!healthy && uint32_t(now-startup)>60000){uint32_t record[]={0x4d4d5631,0};ESP.rtcUserMemoryWrite(0,record,sizeof(record));healthy=true;}
 if(WiFi.status()!=WL_CONNECTED && uint32_t(now-startup)>20000)startFallback();
 if(clockEpoch){uint32_t delta=uint32_t(now-clockTick)/1000;if(delta){clockEpoch+=delta;clockTick+=delta*1000;}}
 updateBrightness();
 madyar::expireFreshData(current,now,received,clockEpoch);
 auto mode=screen.mode(now);
 bool notice=assetsOkay && !recovery && notices.visible(now,mode,screen.diagnostic(now));
 if(mode!=previous)modeSince=now;
 bool animatedMode=mode==madyar::Mode::WORKING || mode==madyar::Mode::WARNING || mode==madyar::Mode::DONE;
 bool reply=assetsOkay && !recovery && current.source_connected && homeReplyVisible(now,mode,screen.diagnostic(now));
 // Reply presentation selects the information view without altering active tasks.
 bool info=reply || (animatedMode && !madyar::animationVisible(mode,uint32_t(now-modeSince),current.animation));
 bool changed=mode!=previous || info!=previousInfo || notice!=previousNotice;
 if(changed){gifStop();if(mode!=madyar::Mode::HOME && !info && !notice && assetsOkay && !recovery){
   if(!gifStart(mode)){assetsOkay=false;homeBegin(false);}
  }
 }
 if(notice){noticeDraw(notices.notice(),notices.elapsed(now),changed,&current.palette);}
 else if(mode==madyar::Mode::HOME || info || !assetsOkay || recovery){
  // Both views already contain the clock: update the footer without blanking
  // the whole screen just because a turn stopped or its reply badge appeared.
  const bool repaint=changed && (previousNotice || !(previous==madyar::Mode::HOME || previousInfo));
  homeDraw(current,screen,now,clockEpoch,received,repaint);
 }else if(!gifTick(now)){assetsOkay=false;homeBegin(false);}
 previous=mode;previousInfo=info;previousNotice=notice;
 yield();delay(1);
}
