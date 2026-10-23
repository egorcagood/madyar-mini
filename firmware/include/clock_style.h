#pragma once
#include <stdint.h>

namespace madyar {
enum class ClockStyle:uint8_t { CLASSIC=0,ROUNDED=1,TYPE=2 };
inline const char* sessionWord(unsigned n){
 if(n%100>=11 && n%100<=14)return "сессий";
 if(n%10==1)return "сессия";
 if(n%10>=2 && n%10<=4)return "сессии";
 return "сессий";
}
}
