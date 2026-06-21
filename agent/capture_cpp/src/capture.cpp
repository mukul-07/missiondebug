// missiondebug_capture: the agent's capture hot path in C++, exposed to Python
// via pybind11. Phase 1: subscribe to configured topics (generic / serialized,
// no deserialize), buffer the raw bytes in a per-topic time-windowed ring
// buffer, and hand a snapshot back to Python on demand.
//
// Python keeps everything else (config, control API, detectors, MCAP writing).
// This module is a drop-in for RosBridge + RingBuffer: snapshot() returns the
// same (topic, ts_ns, wall_ns, payload) shape that RingBuffer.snapshot() does
// and that write_session() consumes.
//
// Phase 1 scope: buffer-only capture + snapshot + parity. Detector callbacks
// and full agent wiring come in Phase 2.

#include <chrono>
#include <deque>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "rclcpp/rclcpp.hpp"
#include "rclcpp/serialized_message.hpp"

namespace py = pybind11;

namespace {

int64_t steady_ns() {
  return std::chrono::duration_cast<std::chrono::nanoseconds>(
             std::chrono::steady_clock::now().time_since_epoch())
      .count();
}

int64_t wall_ns() {
  return std::chrono::duration_cast<std::chrono::nanoseconds>(
             std::chrono::system_clock::now().time_since_epoch())
      .count();
}

}  // namespace

// One buffered message: capture timestamps + the raw serialized CDR bytes.
struct BufferedMessage {
  int64_t timestamp_ns;  // monotonic, for ordering/eviction
  int64_t wall_ns;       // wall clock, for MCAP log_time
  std::string topic;
  std::string payload;   // owns the serialized bytes
};

// Per-topic config the operator/agent passes from Python.
struct TopicSpec {
  std::string name;
  std::string type;          // e.g. "sensor_msgs/msg/CompressedImage"
  std::string reliability;   // "", "reliable", or "best_effort"
  int queue_depth = 10;
  int rate_divisor = 1;      // keep every Nth message
  double ring_seconds = 0;   // 0 = use the global window
};

class TopicBuffer {
 public:
  explicit TopicBuffer(int64_t window_ns) : window_ns_(window_ns) {}

  void append(BufferedMessage&& msg) {
    total_bytes_ += msg.payload.size();
    int64_t cutoff = msg.timestamp_ns - window_ns_;
    items_.push_back(std::move(msg));
    while (!items_.empty() && items_.front().timestamp_ns < cutoff) {
      total_bytes_ -= items_.front().payload.size();
      items_.pop_front();
    }
  }

  std::deque<BufferedMessage> items_;
  int64_t total_bytes_ = 0;
  int64_t window_ns_;
};

class Capture {
 public:
  Capture(std::vector<TopicSpec> topics, double buffer_seconds,
          int64_t max_total_bytes)
      : topics_(std::move(topics)),
        default_window_ns_(static_cast<int64_t>(buffer_seconds * 1e9)),
        max_total_bytes_(max_total_bytes) {
    if (!rclcpp::ok()) {
      rclcpp::init(0, nullptr);
    }
    node_ = std::make_shared<rclcpp::Node>("missiondebug_capture");
    for (const auto& t : topics_) {
      int64_t window = t.ring_seconds > 0
                           ? static_cast<int64_t>(t.ring_seconds * 1e9)
                           : default_window_ns_;
      buffers_.emplace(t.name, std::make_unique<TopicBuffer>(window));
      subscribe(t);
    }
  }

  void start() {
    if (spinning_) return;
    spinning_ = true;
    spin_thread_ = std::thread([this]() {
      exec_.add_node(node_);
      exec_.spin();
    });
  }

  void stop() {
    if (!spinning_) return;
    exec_.cancel();
    if (spin_thread_.joinable()) spin_thread_.join();
    spinning_ = false;
  }

  // Snapshot: all buffered messages across all topics, sorted by timestamp.
  // Returned to Python as a list of (topic, ts_ns, wall_ns, bytes).
  std::vector<py::tuple> snapshot() {
    std::vector<BufferedMessage> merged;
    {
      std::lock_guard<std::mutex> lk(mu_);
      for (auto& [name, buf] : buffers_) {
        for (auto& m : buf->items_) merged.push_back(m);
      }
    }
    std::sort(merged.begin(), merged.end(),
              [](const BufferedMessage& a, const BufferedMessage& b) {
                return a.timestamp_ns < b.timestamp_ns;
              });
    std::vector<py::tuple> out;
    out.reserve(merged.size());
    for (auto& m : merged) {
      out.push_back(py::make_tuple(
          m.topic, m.timestamp_ns, m.wall_ns,
          py::bytes(m.payload)));
    }
    return out;
  }

  size_t buffer_size() {
    std::lock_guard<std::mutex> lk(mu_);
    size_t n = 0;
    for (auto& [name, buf] : buffers_) n += buf->items_.size();
    return n;
  }

 private:
  void subscribe(const TopicSpec& t) {
    rclcpp::QoS qos(rclcpp::KeepLast(t.queue_depth));
    if (t.reliability == "best_effort") {
      qos.best_effort();
    } else if (t.reliability == "reliable") {
      qos.reliable();
    }
    int divisor = t.rate_divisor;
    std::string name = t.name;
    auto counter = std::make_shared<int>(0);
    auto sub = node_->create_generic_subscription(
        t.name, t.type, qos,
        [this, name, divisor, counter](
            std::shared_ptr<rclcpp::SerializedMessage> msg) {
          if (divisor > 1) {
            int n = (*counter)++;
            if (n % divisor != 0) return;
          }
          const auto& rcl = msg->get_rcl_serialized_message();
          BufferedMessage bm;
          bm.timestamp_ns = steady_ns();
          bm.wall_ns = wall_ns();
          bm.topic = name;
          bm.payload.assign(reinterpret_cast<const char*>(rcl.buffer),
                            rcl.buffer_length);
          std::lock_guard<std::mutex> lk(mu_);
          auto it = buffers_.find(name);
          if (it != buffers_.end()) {
            it->second->append(std::move(bm));
            enforce_global_budget_locked();
          }
        });
    subs_.push_back(sub);
  }

  // Drop oldest across all topics until under the global byte cap.
  void enforce_global_budget_locked() {
    if (max_total_bytes_ <= 0) return;
    int64_t total = 0;
    for (auto& [name, buf] : buffers_) total += buf->total_bytes_;
    while (total > max_total_bytes_) {
      TopicBuffer* oldest = nullptr;
      int64_t oldest_ts = 0;
      for (auto& [name, buf] : buffers_) {
        if (buf->items_.empty()) continue;
        int64_t head = buf->items_.front().timestamp_ns;
        if (oldest == nullptr || head < oldest_ts) {
          oldest_ts = head;
          oldest = buf.get();
        }
      }
      if (oldest == nullptr) break;
      total -= oldest->items_.front().payload.size();
      oldest->total_bytes_ -= oldest->items_.front().payload.size();
      oldest->items_.pop_front();
    }
  }

  std::vector<TopicSpec> topics_;
  int64_t default_window_ns_;
  int64_t max_total_bytes_;
  std::shared_ptr<rclcpp::Node> node_;
  std::vector<rclcpp::GenericSubscription::SharedPtr> subs_;
  std::map<std::string, std::unique_ptr<TopicBuffer>> buffers_;
  rclcpp::executors::SingleThreadedExecutor exec_;
  std::thread spin_thread_;
  std::mutex mu_;
  bool spinning_ = false;
};

PYBIND11_MODULE(missiondebug_capture, m) {
  m.doc() = "C++ capture hot path for the MissionDebug agent (Phase 1).";

  py::class_<TopicSpec>(m, "TopicSpec")
      .def(py::init<>())
      .def_readwrite("name", &TopicSpec::name)
      .def_readwrite("type", &TopicSpec::type)
      .def_readwrite("reliability", &TopicSpec::reliability)
      .def_readwrite("queue_depth", &TopicSpec::queue_depth)
      .def_readwrite("rate_divisor", &TopicSpec::rate_divisor)
      .def_readwrite("ring_seconds", &TopicSpec::ring_seconds);

  py::class_<Capture>(m, "Capture")
      .def(py::init<std::vector<TopicSpec>, double, int64_t>(),
           py::arg("topics"), py::arg("buffer_seconds"),
           py::arg("max_total_bytes") = 0)
      .def("start", &Capture::start)
      .def("stop", &Capture::stop)
      .def("snapshot", &Capture::snapshot)
      .def("buffer_size", &Capture::buffer_size);
}
