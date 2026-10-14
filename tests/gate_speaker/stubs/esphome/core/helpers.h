#pragma once
#include <functional>
#include <vector>
namespace esphome {
template<typename> class CallbackManager;
template<typename... Ts> class CallbackManager<void(Ts...)> {
 public:
  void add(std::function<void(Ts...)> &&f) { this->cbs_.push_back(std::move(f)); }
  void call(Ts... args) { for (auto &f : this->cbs_) f(args...); }
 protected:
  std::vector<std::function<void(Ts...)>> cbs_;
};
}  // namespace esphome
