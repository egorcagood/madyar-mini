#pragma once
#include <stdint.h>
namespace madyar {
template<typename Service,typename Clock> uint32_t serviceAndSample(Service service,Clock clock){
 service();return clock();
}
}
