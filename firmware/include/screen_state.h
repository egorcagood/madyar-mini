#pragma once
#include <stdint.h>

namespace madyar {
enum class Mode : uint8_t { HOME=0, WORKING=1, WARNING=2, DONE=3, DIAGNOSTIC=4 };
struct Packet {
  uint32_t boot=0, owner=0, seq=0, lease_ms=30000, done_ms=10000;
  Mode mode=Mode::HOME;
  uint8_t active=0, waiting=0;
};

class ScreenState {
  uint32_t boot_, owner_=0, seq_=0, seen_=0, lease_=30000, done_start_=0, done_ms_=10000;
  Mode selected_=Mode::HOME;
  uint8_t active_=0, waiting_=0;
  bool have_packet_=false,done_expired_=false;
public:
  explicit ScreenState(uint32_t boot):boot_(boot){}
  void reset(uint32_t boot) { *this=ScreenState(boot); }
  void tick(uint32_t now) {
    if(owner_ && uint32_t(now-seen_)>=lease_){
      owner_=0;seq_=0;have_packet_=false;selected_=Mode::HOME;active_=waiting_=0;
    }
    if(selected_==Mode::DONE && uint32_t(now-done_start_)>=done_ms_)done_expired_=true;
  }
  bool connected(uint32_t now) { tick(now);return owner_ && have_packet_; }
  bool claimed(uint32_t now) { tick(now);return owner_!=0; }
  bool claim(uint32_t owner,uint32_t now) {
    if (!owner || claimed(now)) return false;
    owner_=owner; seq_=0; seen_=now; lease_=30000; have_packet_=false;
    selected_=Mode::HOME; active_=waiting_=0; return true;
  }
  bool accept(const Packet& p,uint32_t now) {
    if (!claimed(now) || p.boot!=boot_ || p.owner!=owner_ || p.seq<=seq_ ||
        !p.seq || p.lease_ms<1000 || p.lease_ms>120000 || p.done_ms<1000 || p.done_ms>60000 ||
        p.active>8 || p.waiting>8 || p.active+p.waiting>8 || uint8_t(p.mode)>4) return false;
    if ((p.mode==Mode::WORKING && (!p.active || p.waiting)) ||
        (p.mode==Mode::WARNING && !p.waiting) ||
        ((p.mode==Mode::DONE || p.mode==Mode::HOME) && (p.active || p.waiting))) return false;
    if (p.mode==Mode::DONE && selected_!=Mode::DONE) { done_start_=now; done_ms_=p.done_ms;done_expired_=false; }
    if(p.mode!=Mode::DONE)done_expired_=false;
    selected_=p.mode; active_=p.active; waiting_=p.waiting; seen_=now;
    lease_=p.lease_ms; seq_=p.seq; have_packet_=true;
    return true;
  }
  Mode mode(uint32_t now) {
    if (!connected(now) || selected_==Mode::DIAGNOSTIC) return Mode::HOME;
    if (selected_==Mode::DONE && done_expired_) return Mode::HOME;
    return selected_;
  }
  bool diagnostic(uint32_t now) { return !connected(now) || selected_==Mode::DIAGNOSTIC; }
  uint8_t active() const { return active_; }
  uint8_t waiting() const { return waiting_; }
  uint32_t seq() const { return seq_; }
  uint32_t boot() const { return boot_; }
};
}
