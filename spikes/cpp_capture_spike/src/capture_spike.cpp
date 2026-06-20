// CPU spike: the C++ equivalent of the agent's capture hot path.
//
// Subscribes to one topic as a SerializedMessage (no deserialize into a typed
// C++ object, same as our agent buffers raw bytes), copies the bytes into a
// time-windowed ring buffer, and does nothing else. The point is to measure
// this process's CPU under an identical publisher load and compare it against
// the Python rclpy agent doing the same per-message work.
//
// This is a throwaway measurement node, NOT the real capture module. It exists
// only to answer: does a C++ subscriber use materially less CPU than Python?

#include <chrono>
#include <deque>
#include <memory>
#include <mutex>
#include <string>

#include "rclcpp/rclcpp.hpp"
#include "rclcpp/serialized_message.hpp"

using namespace std::chrono_literals;

// A buffered message: capture timestamp + the raw serialized bytes.
struct BufferedMessage {
  int64_t stamp_ns;
  std::string payload;  // owns the bytes (std::string is a fine byte container)
};

class CaptureSpike : public rclcpp::Node {
 public:
  CaptureSpike(const std::string & topic, double window_seconds)
      : Node("capture_spike"), window_ns_(static_cast<int64_t>(window_seconds * 1e9)) {
    // Best-effort QoS to match how sensor streams (camera/lidar) publish, the
    // same choice the Python agent makes for high-rate topics.
    rclcpp::QoS qos(rclcpp::KeepLast(10));
    qos.best_effort();

    // Generic subscription: we receive a SerializedMessage and never deserialize
    // into a typed message. This is the raw-bytes capture path in C++.
    sub_ = this->create_generic_subscription(
        topic, "sensor_msgs/msg/CompressedImage", qos,
        [this](std::shared_ptr<rclcpp::SerializedMessage> msg) {
          this->on_message(*msg);
        });

    // Report buffer size every 5s so we can confirm it is actually receiving.
    timer_ = this->create_wall_timer(5s, [this]() {
      std::lock_guard<std::mutex> lk(mu_);
      RCLCPP_INFO(this->get_logger(), "buffer: %zu msgs", buf_.size());
    });

    RCLCPP_INFO(this->get_logger(), "capturing %s (best_effort, %.0fs window)",
                topic.c_str(), window_seconds);
  }

 private:
  void on_message(const rclcpp::SerializedMessage & msg) {
    const auto & rcl = msg.get_rcl_serialized_message();
    int64_t now = std::chrono::duration_cast<std::chrono::nanoseconds>(
                      std::chrono::steady_clock::now().time_since_epoch())
                      .count();
    std::lock_guard<std::mutex> lk(mu_);
    // Copy the serialized bytes into the buffer (this is the per-message work).
    buf_.push_back(BufferedMessage{
        now,
        std::string(reinterpret_cast<const char *>(rcl.buffer), rcl.buffer_length)});
    // Evict anything older than the window (time-windowed ring buffer).
    int64_t cutoff = now - window_ns_;
    while (!buf_.empty() && buf_.front().stamp_ns < cutoff) {
      buf_.pop_front();
    }
  }

  int64_t window_ns_;
  std::deque<BufferedMessage> buf_;
  std::mutex mu_;
  rclcpp::GenericSubscription::SharedPtr sub_;
  rclcpp::TimerBase::SharedPtr timer_;
};

int main(int argc, char ** argv) {
  rclcpp::init(argc, argv);
  std::string topic = (argc > 1) ? argv[1] : "/camera/image_raw/compressed";
  rclcpp::spin(std::make_shared<CaptureSpike>(topic, 60.0));
  rclcpp::shutdown();
  return 0;
}
