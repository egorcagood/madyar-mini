#include "display.h"
#include "quota_reset.h"
#include "reply_state.h"
#include <LittleFS.h>
#include <ESP8266WiFi.h>
#include <time.h>
#include <algorithm>

TFT_eSPI tft;
static File fontFile,motion;
static bool resources=false;
static bool suspended=false;
static madyar::ReplyState replies;
static bool lastReply=false;
void homeReplyAccept(uint32_t id,uint32_t ttl_ms,uint32_t now){replies.accept(id,ttl_ms,now);}
void homeReplyClear(){replies.clearPresentation();}
bool homeReplyVisible(uint32_t now,madyar::Mode mode,bool diagnostic){return replies.visible(now,mode,diagnostic);}
static uint32_t noticeFrame=UINT32_MAX;
static uint32_t noticeId=0;
static unsigned noticePage=0,noticePages=1;
static uint32_t lastSecond=UINT32_MAX,lastFrame=UINT32_MAX;
static uint32_t weatherKey=0,clockKey=0,quotaKey=0,footerKey=0,ipKey=0;
static uint32_t weatherIconKey=0,weatherDegreeKey=0;
static madyar::ClockStyle lastClockStyle=madyar::ClockStyle::CLASSIC;
static uint32_t viewKey(const char* value,uint32_t seed=2166136261u){
 while(*value){seed^=uint8_t(*value++);seed*=16777619u;}return seed;
}
static uint32_t offsets[145];
static const uint16_t BG=0x0042,WHITE=0xefdf,BLUE=0x465e,SOFT=0xae5b;
static void text(const char* p,int x,int y,uint16_t color,int maxWidth);
static madyar::Palette palette;
static madyar::Palette noticeTheme;
static bool ipInitialized=false,lastShowIp=true,ipAnimating=false;
static uint32_t ipHideStart=0,ipFrame=UINT32_MAX;
static uint16_t gradient(uint32_t from,uint32_t to,int position,int extent){
 position=std::max(0,std::min(position,extent));uint32_t rgb=0;
 for(int shift=0;shift<=16;shift+=8){int a=(from>>shift)&255,b=(to>>shift)&255;rgb|=uint32_t(a+(b-a)*position/extent)<<shift;}
 return uint16_t(((rgb>>8)&0xf800)|((rgb>>5)&0x07e0)|((rgb>>3)&31));
}
static uint16_t background(int x,int y){return gradient(palette.background_from,palette.background_to,x+y,478);}
static uint16_t rgb565(uint32_t rgb){return uint16_t(((rgb>>8)&0xf800)|((rgb>>5)&0x07e0)|((rgb>>3)&31));}
// The immutable M frames contain a dark baked backdrop. Blend low-intensity
// backdrop/glow into the current gradient; keep bright metal pixels unchanged.
static uint16_t composeM(uint16_t source,int x,int y){
 const uint16_t target=background(x,y);
 const int r=(source>>11)&31,g=(source>>5)&63,b=source&31;
 const int peak=std::max(g*255/63,b*255/31);
 const int coverage=std::max(0,std::min(255,(peak-16)*255/72));
 const int inverse=255-coverage;
 const int rr=std::max(0,std::min(31,r+int((target>>11)&31)*inverse/255));
 const int gg=std::max(0,std::min(63,g+(int((target>>5)&63)-2)*inverse/255));
 const int bb=std::max(0,std::min(31,b+(int(target&31)-2)*inverse/255));
 return uint16_t((rr<<11)|(gg<<5)|bb);
}
static void weatherIcon(const madyar::Snapshot& data){
 if(!strcmp(data.weather_kind,"sun")){
  for(int i=-1;i<=1;++i)for(int j=-1;j<=1;++j)if(i||j)
   tft.drawLine(199+i*13,(32+j*13-2),199+i*17,(32+j*17-2),0xff56);
  tft.fillCircle(199,30,9,0xff56);tft.fillCircle(196,27,4,WHITE);return;
 }
 const bool overcast=!strcmp(data.weather,"Пасмурно");
 if(overcast){tft.fillCircle(194,22,7,SOFT);tft.fillCircle(204,18,8,SOFT);tft.fillRoundRect(186,23,29,9,4,SOFT);}
 tft.fillCircle(189,30,7,SOFT);tft.fillCircle(199,25,9,overcast?SOFT:WHITE);
 tft.fillCircle(209,30,7,SOFT);tft.fillRoundRect(182,30,34,10,4,SOFT);
 if(!strcmp(data.weather_kind,"rain"))for(int i=0;i<3;++i){
  tft.drawLine(189+i*9,43,185+i*9,51,BLUE);tft.drawLine(190+i*9,43,186+i*9,51,BLUE);
 }
 if(!strcmp(data.weather_kind,"snow"))for(int x:{189,199,209}){
  tft.drawLine(x-3,46,x+3,46,WHITE);tft.drawLine(x,43,x,49,WHITE);
  tft.drawLine(x-2,44,x+2,48,WHITE);tft.drawLine(x-2,48,x+2,44,WHITE);
 }
 if(!strcmp(data.weather,"Туман"))for(int y:{44,48,52})tft.drawLine(184,(y-2),214,(y-2),SOFT);
}
static void clearBackground(int x,int y,int w,int h,const madyar::Palette* theme=nullptr){
 uint16_t line[240];
 const auto& selected=theme?*theme:palette;
 for(int yy=y;yy<y+h;++yy){for(int xx=0;xx<w;++xx)line[xx]=gradient(selected.background_from,selected.background_to,x+xx+yy,478);tft.pushImage(x,yy,w,1,line);yield();}
}
static String visibleIp(const char* label){return label && *label?String(label):(WiFi.status()==WL_CONNECTED?WiFi.localIP().toString():WiFi.softAPIP().toString());}
static void ipStripe(bool show,uint32_t now,bool force,int y=30,const char* label=""){
 if(!ipInitialized){ipInitialized=true;lastShowIp=show;}
 if(show!=lastShowIp){ipAnimating=!show;ipHideStart=now;ipFrame=UINT32_MAX;lastShowIp=show;force=true;}
 const uint32_t age=now-ipHideStart,frame=age/100;
 if(ipAnimating && age>=800){ipAnimating=false;force=true;}
 if(!force && (!ipAnimating || frame==ipFrame))return;
 ipFrame=frame;clearBackground(10,y,176,17);
 if(show){String ip="IP "+visibleIp(label);text(ip.c_str(),10,y,SOFT,176);}
 else if(ipAnimating){
  // Independent synthetic data: never read, transform or cache a real address.
  char noise[17]{};uint32_t seed=0x9e3779b9u^(frame*0x45d9f3bu);
  int count=16-int(frame)*2;for(int i=0;i<count;++i){seed=1664525u*seed+1013904223u;noise[i]="0123456789ABCDEF"[(seed>>24)&15];}
  text(noise,10,y,BLUE,176);
 }
}
void homeSuspend(){suspended=true;fontFile.close();motion.close();}
static uint16_t read16(File& f){uint8_t b[2];return f.read(b,2)==2?uint16_t(b[0]|b[1]<<8):0;}
static uint32_t read32(File& f){uint8_t b[4];return f.read(b,4)==4?uint32_t(b[0])|(uint32_t(b[1])<<8)|(uint32_t(b[2])<<16)|(uint32_t(b[3])<<24):0;}
static uint32_t nextCode(const char*& p){
 uint8_t c=*p++;if(c<128)return c;
 int n=c<224?1:(c<240?2:3);uint32_t v=c&((1<<(6-n))-1);
 while(n-- && *p)v=(v<<6)|(uint8_t(*p++)&63);
 return v;
}
// font.bin is a hash-verified, sorted immutable table. Binary lookup keeps
// long Cyrillic notifications from repeatedly scanning all 165 records.
static bool findGlyph(uint32_t code,int& advance,uint8_t* glyph=nullptr){
 if(!fontFile)return false;
 fontFile.seek(4);int lo=0,hi=read16(fontFile);
 while(lo<hi){int mid=(lo+hi)/2;fontFile.seek(8+mid*37);uint16_t cp=read16(fontFile);
  if(cp<code)lo=mid+1;
  else if(cp>code)hi=mid;
  else {advance=fontFile.read();return !glyph || fontFile.read(glyph,34)==34;}
 }
 return false;
}
static void text(const char* p,int x,int y,uint16_t color,int maxWidth=220){
 int start=x;
 if(!fontFile)return;
 while(*p){
  uint32_t code=nextCode(p);
  uint8_t glyph[34];int advance=7;
  bool found=findGlyph(code,advance,glyph);
  if(x+advance-start>maxWidth)return;
  if(found)for(int yy=0;yy<17;++yy)for(int xx=0;xx<16;++xx)
   if(glyph[yy*2+xx/8]&(128>>(xx%8)))tft.drawPixel(x+xx,y+yy,color);
  x+=advance;
 }
}
void homeBegin(bool mounted){
 suspended=false;noticeFrame=UINT32_MAX;
 resources=mounted;fontFile.close();motion.close();
 if(mounted){fontFile=LittleFS.open("/font.bin","r");motion=LittleFS.open("/m.rle","r");}
 char magic[4]{};
 if(motion && motion.readBytes(magic,4)==4 && !memcmp(magic,"MMR1",4) &&
    read16(motion)==78 && read16(motion)==75 && read16(motion)==144 && read16(motion)==8){
  for(auto& o:offsets)o=read32(motion);
  for(int i=0;i<144;++i)if(offsets[i]>=offsets[i+1])resources=false;
  if(offsets[144]+592!=motion.size())resources=false;
 }else resources=false;
 lastSecond=lastFrame=UINT32_MAX;
}
static int advanceFor(uint32_t code){
 int advance=7;findGlyph(code,advance);return advance;
}
static int textWidth(const char* p,int maxWidth){
 int width=0;
 while(*p){int advance=advanceFor(nextCode(p));if(width+advance>maxWidth)break;width+=advance;}
 return width;
}
static void largeText(const char* p,int x,int y,uint16_t color,int height){
 if(height==17){text(p,x,y,color,204);return;}
 const int start=x;
 while(*p){uint8_t glyph[34];int advance=7;const bool found=findGlyph(nextCode(p),advance,glyph);
  int step=(advance*height+16)/17;if(x+step-start>204)break;
  if(found)for(int yy=0;yy<17;++yy)for(int xx=0;xx<16;++xx)if(glyph[yy*2+xx/8]&(128>>(xx%8)))
   tft.fillRect(x+xx*height/17,y+yy*height/17,(xx+1)*height/17-xx*height/17,(yy+1)*height/17-yy*height/17,color);
  x+=step;
 }
}
// Bounded word wrapping keeps UTF-8 intact, with no framebuffer or message cache.
static unsigned wrapText(const char* input,unsigned first,unsigned visible,int y,uint16_t color,int height=17,bool centered=false){
 const char* p=input;unsigned lineNumber=0;
 while(*p){
  while(*p==' ')++p;
  if(!*p)break;
  const char* begin=p;const char* end=p;const char* space=nullptr;const char* afterSpace=nullptr;int width=0,spaceWidth=0;
  while(*p){const char* next=p;uint32_t code=nextCode(next);int advance=(advanceFor(code)*height+16)/17;
   if(width+advance>204 && p!=begin){if(space){end=space;p=afterSpace;width=spaceWidth;}break;}
   if(code==' '){space=p;afterSpace=next;spaceWidth=width;}
   width+=advance;p=next;end=p;
  }
  if(lineNumber>=first && lineNumber<first+visible){
   char line[289];size_t bytes=size_t(end-begin);memcpy(line,begin,bytes);line[bytes]=0;
   largeText(line,centered?(240-width)/2:18,y+int(lineNumber-first)*(height+3),color,height);
  }
  ++lineNumber;
  yield();
 }
 return lineNumber;
}
static void noticeCutout(int x,int y,int xx,int yy){
 const int dx=abs(xx-x),sx=x<xx?1:-1,dy=-abs(yy-y),sy=y<yy?1:-1;int err=dx+dy;
 for(;;){tft.drawPixel(x,y,gradient(noticeTheme.background_from,noticeTheme.background_to,x+y,478));
  if(x==xx && y==yy)break;
  const int twice=2*err;
  if(twice>=dy){err+=dy;x+=sx;}if(twice<=dx){err+=dx;y+=sy;}}
}
void noticeDraw(const madyar::Notice& notice,uint32_t elapsed,bool force,const madyar::Palette* theme){
 if(suspended)return;
 const uint32_t frame=elapsed/50;
 const auto& selected=theme?*theme:palette;
 const bool fresh=force || noticeId!=notice.id || memcmp(&noticeTheme,&selected,sizeof(selected));
 if(!fresh && frame==noticeFrame)return;
 noticeFrame=frame;noticeId=notice.id;
 if(fresh){
  noticeTheme=selected;
  clearBackground(0,0,240,240,&noticeTheme);tft.drawRoundRect(8,8,224,224,10,rgb565(noticeTheme.frame));
  if(!fontFile){tft.setTextColor(WHITE,rgb565(noticeTheme.background_from));tft.drawString("Telegram",18,17,2);return;}
  text("Telegram",18,17,BLUE,160);
  const unsigned titleLines=wrapText(notice.title,0,0,45,WHITE,20,true);
  wrapText(notice.title,0,2,titleLines<=1?58:45,WHITE,20,true);
  const unsigned lines=wrapText(notice.body,0,0,91,WHITE,24,true);noticePages=std::max(1u,(lines+3)/4);
 }
 const unsigned page=(elapsed/4000)%noticePages;
 if(fresh || page!=noticePage){
  clearBackground(18,89,204,139,&noticeTheme);wrapText(notice.body,page*4,4,91,WHITE,24,true);
  char footer[32];snprintf(footer,sizeof(footer),"%u/%u",page+1,noticePages);text(footer,18,211,BLUE,204);noticePage=page;
 }
 clearBackground(191,12,37,31,&noticeTheme);
 static const int8_t xPath[48]={0,0,1,1,1,2,2,2,3,3,3,3,3,3,3,3,3,2,2,2,1,1,1,0,0,0,-1,-1,-1,-2,-2,-2,-3,-3,-3,-3,-3,-3,-3,-3,-3,-2,-2,-2,-2,-1,-1,0};
 static const int8_t yPath[48]={-2,-2,-2,-2,-2,-2,-1,-1,-1,-1,-1,0,0,0,1,1,1,1,1,2,2,2,2,2,2,2,2,2,2,2,1,1,1,1,1,0,0,0,-1,-1,-1,-1,-1,-2,-2,-2,-2,-2};
 const int dx=xPath[frame%48],dy=yPath[frame%48];
 tft.fillTriangle(195+dx,22+dy,222+dx,14+dy,211+dx,39+dy,BLUE);
 noticeCutout(202+dx,26+dy,216+dx,19+dy);noticeCutout(202+dx,26+dy,211+dx,34+dy);
}
static void drawM(uint32_t index){
 if(!resources)return;
 const auto end=offsets[index+1]+592;
 if(!motion.seek(offsets[index]+592))return;
 uint16_t line[78],scaled[66];int lastY=-1;int run=0;uint16_t color=0;
 for(int y=0;y<75;++y){
  for(int x=0;x<78;++x){
   if(!run){
    if(motion.position()+3>end){resources=false;return;}
    run=motion.read();color=read16(motion);if(!run){resources=false;return;}
   }
   line[x]=color;--run;
  }
  const int outY=y*63/75;
  if(outY!=lastY){for(int x=0;x<66;++x)scaled[x]=composeM(line[x*78/66],167+x,137+outY);tft.pushImage(167,137+outY,66,1,scaled);lastY=outY;}
  yield();
 }
 if(run || motion.position()!=end)resources=false;
}
static void digitClassic(int value,int x,int y){
 static const char* codes[]={"abcdef","bc","abged","abgcd","fgbc","afgcd","afgecd","abc","abcdefg","abfgcd"};
 static const int8_t polys[7][6][2]={
  {{3,0},{27,0},{30,3},{24,7},{6,7},{0,3}},
  {{30,5},{23,8},{23,20},{27,24},{30,21},{30,21}},
  {{30,29},{27,26},{23,30},{23,42},{30,45},{30,45}},
  {{5,43},{25,43},{30,47},{27,50},{3,50},{0,47}},
  {{0,29},{3,26},{7,30},{7,42},{0,45},{0,45}},
  {{0,5},{7,8},{7,20},{3,24},{0,21},{0,21}},
  {{5,22},{25,22},{29,25},{25,28},{5,28},{1,25}}};
 if(value<0 || value>9)return;
 // Scan one 31px row of the existing segment polygons; no screen buffer.
 uint16_t line[31];bool mask[31];
 for(int row=0;row<43;++row){const int yy=row*51/43;
  memset(mask,0,sizeof(mask));
  for(const char* key=codes[value];*key;++key){auto& p=polys[*key-'a'];
   for(int i=1;i<5;++i){int ax=p[0][0],ay=p[0][1],bx=p[i][0],by=p[i][1],cx=p[i+1][0],cy=p[i+1][1];
    if((bx-ax)*(cy-ay)==(by-ay)*(cx-ax))continue;
    for(int xx=0;xx<=30;++xx){int a=(bx-ax)*(yy-ay)-(by-ay)*(xx-ax),b=(cx-bx)*(yy-by)-(cy-by)*(xx-bx),c=(ax-cx)*(yy-cy)-(ay-cy)*(xx-cx);
     if((a>=0&&b>=0&&c>=0)||(a<=0&&b<=0&&c<=0))mask[xx]=true;}
   }
  }
  for(int xx=0;xx<26;++xx)line[xx]=mask[xx*31/26]?gradient(palette.hour_from,palette.hour_to,x+xx-10+row,165):background(x+xx,y+row);
  tft.pushImage(x,y+row,26,1,line);
 }

}
static bool inRoundedBox(int x,int y,int left,int top,int width,int height,int radius){
 if(x<left || x>=left+width || y<top || y>=top+height)return false;
 const int cx=std::max(left+radius,std::min(x,left+width-radius-1));
 const int cy=std::max(top+radius,std::min(y,top+height-radius-1));
 return (x-cx)*(x-cx)+(y-cy)*(y-cy)<=radius*radius;
}
static void digit(int value,int x,int y,madyar::ClockStyle style){
 if(style==madyar::ClockStyle::CLASSIC){digitClassic(value,x,y);return;}
 if(value<0 || value>9)return;
 static const char* codes[]={"abcdef","bc","abged","abgcd","fgbc","afgcd","afgecd","abc","abcdefg","abfgcd"};
 static const uint8_t boxes[7][5]={{4,0,23,7,3},{23,8,7,14,3},{23,30,7,14,3},{4,44,23,7,3},{1,30,7,14,3},{1,8,7,14,3},{4,22,23,7,3}};
 uint8_t glyph[34]{};int advance=9,minY=17,maxY=0;
 if(style==madyar::ClockStyle::TYPE){
  if(!findGlyph(uint32_t('0'+value),advance,glyph))return;
  for(int yy=0;yy<17;++yy)for(int xx=0;xx<16;++xx)if(glyph[yy*2+xx/8]&(128>>(xx%8))){minY=std::min(minY,yy);maxY=std::max(maxY,yy);}
  if(minY>maxY)return;
 }
 uint16_t line[31];
 for(int yy=0;yy<43;++yy){const int sourceY=yy*51/43;
  for(int xx=0;xx<26;++xx){const int sourceX=xx*31/26;
   bool on=false;
   if(style==madyar::ClockStyle::ROUNDED){
    for(const char* key=codes[value];*key;++key){const auto& b=boxes[*key-'a'];on|=inRoundedBox(sourceX,sourceY,b[0],b[1],b[2],b[3],b[4]);}
   }else{
    const int gx=sourceX*advance/31,gy=minY+sourceY*(maxY-minY+1)/51;
    on=gx<16 && (glyph[gy*2+gx/8]&(128>>(gx%8)));
   }
   line[xx]=on?gradient(palette.hour_from,palette.hour_to,x+xx-10+yy,195):background(x+xx,y+yy);
  }
  tft.pushImage(x,y+yy,26,1,line);
 }
}
static void codexMark(){
 tft.drawRoundRect(18,124,21,23,4,BLUE);
 tft.drawLine(23,130,28,135,WHITE);tft.drawLine(28,135,23,140,WHITE);
 tft.drawLine(29,140,34,140,BLUE);
}
static void fitText(const char* value,int x,int y,int height,int width,uint16_t color,bool centered=false){

 while(height>10){int n=0;const char* q=value;while(*q)n+=(advanceFor(nextCode(q))*height+16)/17;if(n<=width)break;--height;}
 int actual=0;char bounded[289]{};size_t bytes=0;const char* p=value;
 while(*p){const char* start=p;const int step=(advanceFor(nextCode(p))*height+16)/17;
  const size_t count=p-start;if(actual+step>width || bytes+count>=sizeof(bounded))break;
  memcpy(bounded+bytes,start,count);bytes+=count;actual+=step;
 }
 largeText(bounded,centered?x+(width-actual)/2:x,y,color,height);
}
// Compose each final scanline in RAM. Never clear a label before painting it:
// the TFT sees background and glyph pixels together, without a blank phase.
static void paintLabel(const char* value,int x,int y,int width,int height,int fontHeight,uint16_t color){
 if(width<1 || width>80 || height<1 || fontHeight<10)return;
 while(fontHeight>10){int measure=0;const char* p=value;while(*p)measure+=(advanceFor(nextCode(p))*fontHeight+16)/17;if(measure<=width)break;--fontHeight;}
 struct Glyph {uint8_t bits[34];int step;bool found;};Glyph glyphs[24];
 unsigned count=0;int total=0;const char* p=value;
 while(*p && count<24){int advance=7;auto& g=glyphs[count];g.found=findGlyph(nextCode(p),advance,g.bits);g.step=(advance*fontHeight+16)/17;if(total+g.step>width)break;total+=g.step;++count;}
 uint16_t line[80];const int start=(width-total)/2;
 for(int row=0;row<height;++row){
  for(int col=0;col<width;++col)line[col]=background(x+col,y+row);
  if(row<fontHeight){const int sourceY=((row+1)*17-1)/fontHeight;int origin=start;
   for(unsigned i=0;i<count;++i){const auto& g=glyphs[i];
    if(g.found)for(int col=0;col<16*fontHeight/17;++col){const int dest=origin+col,sourceX=((col+1)*17-1)/fontHeight;
     if(dest>=0 && dest<width && (g.bits[sourceY*2+sourceX/8]&(128>>(sourceX%8))))line[dest]=color;
    }origin+=g.step;
   }
  }
  tft.pushImage(x,y+row,width,1,line);yield();
 }
}
void homeDraw(const madyar::Snapshot& data,madyar::ScreenState& state,
              uint32_t now,uint32_t epoch,uint32_t received,bool force){
 if(suspended)return;
 const uint32_t second=epoch?epoch:now/1000;
 const bool reply=resources && state.connected(now) && data.source_connected && homeReplyVisible(now,state.mode(now),state.diagnostic(now));
 if(lastSecond==UINT32_MAX)force=true;
 if(memcmp(&palette,&data.palette,sizeof(palette))){palette=data.palette;force=true;}
 if(force)clearBackground(0,0,240,240);
 if(!fontFile){
  if(force || second!=lastSecond){if(force)clearBackground(0,0,240,240);tft.setTextColor(BLUE,BG);
   tft.drawString("Madyar Mini",20,33,2);tft.drawString("Resources not available",20,68,2);
   tft.drawString("Recovery: /update",20,138,2);lastSecond=second;
   ipStripe(data.show_ip,now,true,103,data.ip_text);
  }else ipStripe(data.show_ip,now,false,103,data.ip_text);return;
 }
 if(force || second!=lastSecond || lastClockStyle!=data.clock_style || reply!=lastReply){
  lastReply=reply;
  lastSecond=second;
  // City and country stay available to weather/time-zone settings, not the small screen.
  const uint32_t nextIp=data.show_ip?viewKey(visibleIp(data.ip_text).c_str()):0;
  ipStripe(data.show_ip,now,force || nextIp!=ipKey,96,data.ip_text);ipKey=nextIp;
  const bool weatherFresh=data.weather[0] && uint32_t(now-received)<data.weather_ttl_ms;
  const bool feelsPhase=(now/5000)%2==1;
  const char* degree=weatherFresh?(feelsPhase?data.feels:data.temperature):"—";
  const char* description=weatherFresh?(feelsPhase?"Ощущается":data.weather):"Нет погоды";
  const uint32_t nextIcon=weatherFresh?viewKey(data.weather_kind,viewKey(data.weather)):UINT32_MAX;
  if(force || weatherIconKey!=nextIcon){weatherIconKey=nextIcon;
   clearBackground(158,10,80,44);
   if(weatherFresh)weatherIcon(data);else text("—",194,28,SOFT,20);
  }
  const uint32_t nextDegree=viewKey(degree),nextWeather=viewKey(description);
  if(force || weatherDegreeKey!=nextDegree){weatherDegreeKey=nextDegree;paintLabel(degree,158,54,80,24,22,WHITE);}
  if(force || weatherKey!=nextWeather){weatherKey=nextWeather;paintLabel(description,158,78,80,18,16,SOFT);}
  const uint32_t nextClock=epoch?(uint32_t(int64_t(epoch)+data.utc_offset)/60):UINT32_MAX;
  if(force || clockKey!=nextClock || lastClockStyle!=data.clock_style){clockKey=nextClock;lastClockStyle=data.clock_style;
   clearBackground(10,20,119,43);clearBackground(10,78,132,17);
   if(epoch){time_t local=int64_t(epoch)+data.utc_offset;tm t{};gmtime_r(&local,&t);
    digit(t.tm_hour/10,10,20,data.clock_style);digit(t.tm_hour%10,39,20,data.clock_style);
    digit(t.tm_min/10,73,20,data.clock_style);digit(t.tm_min%10,102,20,data.clock_style);
    tft.fillRect(68,30,4,4,SOFT);tft.fillRect(68,47,4,4,SOFT);
    char buf[32];snprintf(buf,sizeof(buf),"%02d/%02d/%04d",t.tm_mday,t.tm_mon+1,t.tm_year+1900);fitText(buf,10,78,17,110,WHITE);
    const char* days[]={"ВС","ПН","ВТ","СР","ЧТ","ПТ","СБ"};fitText(days[t.tm_wday],122,78,17,24,BLUE);
   }else{text("— : —",25,33,WHITE,120);text("Ожидаем время",10,78,SOFT,132);}
  }
  char seconds[3]{};if(epoch)snprintf(seconds,sizeof(seconds),"%02u",unsigned(epoch%60));
  paintLabel(seconds,132,44,24,24,21,SOFT);
  const bool quotaFresh=data.quota_remaining>=0 && uint32_t(now-received)<data.quota_ttl_ms && epoch && data.quota_resets>epoch;
  char resetHead[40]{},resetBody[40]{};
  if(quotaFresh)madyar::resetCaption(data.quota_resets,epoch,data.utc_offset,false,resetHead,resetBody);
  const uint32_t nextQuota=quotaFresh?viewKey(resetBody,viewKey(resetHead,uint32_t(data.quota_remaining)+(data.quota_window_minutes<<7))):UINT32_MAX;
  if(force || quotaKey!=nextQuota){quotaKey=nextQuota;
   clearBackground(9,114,157,91);tft.drawRoundRect(9,114,157,91,7,rgb565(palette.frame));
   codexMark();largeText("Codex",45,116,WHITE,32);


   tft.fillRoundRect(18,162,75,7,3,rgb565(palette.frame));
   if(quotaFresh){fitText(resetBody,18,179,23,139,SOFT);
    const int fill=75*data.quota_remaining/100;if(fill)tft.fillRoundRect(18,162,fill,7,std::min(3,fill/2),BLUE);
    char value[12];snprintf(value,sizeof(value),"%d%%",data.quota_remaining);fitText(value,98,151,38,61,WHITE);
   }else fitText("Нет данных",18,174,19,139,SOFT);
  }
  String footer;
  if(!resources)footer="Ресурсы недоступны";
  else if(!state.connected(now))footer="Нет связи с ПК";
  else if(!data.source_connected)footer="Codex не подключён";
  else if(reply)footer="Ответ готов";
  else if(state.diagnostic(now))footer="Проверяем состояние";
  else if(state.mode(now)==madyar::Mode::WARNING)footer=(now/3000)%2 && data.label[0]?data.label:"Нужно подтверждение";
  else if(state.mode(now)==madyar::Mode::WORKING){
   char caption[72];snprintf(caption,sizeof(caption),"%s: %u %s",state.active()==1?"Работает":"Работают",unsigned(state.active()),madyar::sessionWord(state.active()));footer=caption;
  }
  else if(state.mode(now)==madyar::Mode::DONE)footer="Задача завершена";
  else footer="Готов к работе";
  const uint32_t nextFooter=viewKey(footer.c_str());
  if(force || footerKey!=nextFooter){footerKey=nextFooter;
  clearBackground(9,211,222,25);tft.drawRoundRect(9,211,222,25,6,reply?gradient(palette.frame,0x54c5d2,1,2):rgb565(palette.frame));
  const int footerWidth=textWidth(footer.c_str(),207);
  text(footer.c_str(),(240-footerWidth)/2,214,BLUE,207);
  }
 }
 ipStripe(data.show_ip,now,false,96,data.ip_text);
 uint32_t index=(now/125)%144;
 if(force || index!=lastFrame){drawM(index);lastFrame=index;}
}





