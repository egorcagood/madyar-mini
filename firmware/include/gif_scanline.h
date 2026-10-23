#pragma once
#include "AnimatedGIF.h"
namespace madyar {
template<typename Emit> bool drawGifLine(GIFDRAW* d,Emit emit) {
  if(!d || d->iWidth<1 || d->iWidth>240 || d->iX<0 || d->iX+d->iWidth>240 ||
     d->iY+d->y<0 || d->iY+d->y>=240 || !d->pPixels || !d->pPalette)return false;
  uint16_t line[240];
  int x=0;
  while(x<d->iWidth) {
    while(x<d->iWidth && d->ucHasTransparency && d->ucDisposalMethod!=2 &&
          d->pPixels[x]==d->ucTransparent)++x;
    const int start=x;
    while(x<d->iWidth) {
      uint8_t index=d->pPixels[x];
      if(d->ucHasTransparency && index==d->ucTransparent) {
        if(d->ucDisposalMethod!=2)break;
        index=d->ucBackground;
      }
      line[x-start]=d->pPalette[index];++x;
    }
    if(x>start)emit(d->iX+start,d->iY+d->y,line,x-start);
  }
  return true;
}
}
