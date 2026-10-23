#include <LittleFS.h>
#include <AnimatedGIF.h>
#include "display.h"
#include "gif_scanline.h"
static AnimatedGIF decoder;
static File source;
static bool opened=false,failed=false;
static uint32_t nextFrame=0;
static void* openFile(const char* name,int32_t* size) {
  source=LittleFS.open(name,"r");if(!source)return nullptr;
  *size=source.size();return &source;
}
static void closeFile(void*){if(source)source.close();}
static int32_t readFile(GIFFILE* file,uint8_t* dest,int32_t n) {
  if(n<0 || file->iPos>file->iSize)return 0;
  int32_t left=file->iSize-file->iPos;if(n>left)n=left;
  auto got=source.read(dest,n);file->iPos=source.position();return got;
}
static int32_t seekFile(GIFFILE* file,int32_t p) {
  if(p<0 || p>file->iSize || !source.seek(p))return -1;
  file->iPos=source.position();return file->iPos;
}
static void drawLine(GIFDRAW* d) {
  if(!madyar::drawGifLine(d,[](int x,int y,const uint16_t* pixels,int n){
    tft.pushImage(x,y,n,1,const_cast<uint16_t*>(pixels));
  }))failed=true;
  yield();
}
void gifStop(){if(opened)decoder.close();opened=false;failed=false;}
bool gifStart(madyar::Mode mode){
  gifStop();const char* name=nullptr;
  if(mode==madyar::Mode::WORKING)name="/working.gif";
  if(mode==madyar::Mode::WARNING)name="/warning.gif";
  if(mode==madyar::Mode::DONE)name="/done.gif";
  if(!name)return false;
  decoder.begin(LITTLE_ENDIAN_PIXELS);
  opened=decoder.open(name,openFile,closeFile,readFile,seekFile,drawLine);
  if(!opened)return false;
  if(decoder.getCanvasWidth()!=240 || decoder.getCanvasHeight()!=240){gifStop();return false;}
  nextFrame=millis();return true;
}
bool gifTick(uint32_t now){
  if(!opened || failed)return false;
  if(int32_t(now-nextFrame)<0)return true;
  int wait=0;const auto begin=millis();
  int rc=decoder.playFrame(false,&wait);
  if(decoder.getLastError()!=GIF_SUCCESS || failed){gifStop();return false;}
  if(!rc)decoder.reset();
  nextFrame=begin+uint32_t(wait>0?wait:160);
  return true;
}
