cmake_minimum_required(VERSION 3.8)
project(cpp_capture_spike)

if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 17)
endif()

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)

add_executable(capture_spike src/capture_spike.cpp)
ament_target_dependencies(capture_spike rclcpp)

install(TARGETS capture_spike DESTINATION lib/${PROJECT_NAME})

ament_package()
