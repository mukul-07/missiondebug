cmake_minimum_required(VERSION 3.8)
project(missiondebug_capture)

if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 17)
endif()

# ament_cmake is optional: present under a colcon build (dev loop), absent for a
# plain `cmake` build (the .deb packaging path, packaging/build-capture-so.sh).
# rclcpp + pybind11 are found the same way either way as long as a ROS 2 env is
# sourced (CMAKE_PREFIX_PATH points at /opt/ros/<distro>).
find_package(ament_cmake QUIET)
find_package(rclcpp REQUIRED)
# pybind11's new tools call python3_add_library, which only exists after
# FindPython3 is loaded with the Development component. Find it first.
find_package(Python3 REQUIRED COMPONENTS Interpreter Development)
find_package(pybind11_vendor QUIET)
find_package(pybind11 REQUIRED)

# Build the Python extension module. pybind11_add_module produces a correctly
# named, suffixed shared object (missiondebug_capture.cpython-310-*.so).
pybind11_add_module(missiondebug_capture src/capture.cpp)
# Link rclcpp with the KEYWORD form (PRIVATE). pybind11/FindPython already use
# the keyword signature on this target, and CMake forbids mixing keyword and
# plain target_link_libraries on one target, so ament_target_dependencies
# (which uses the plain form) cannot be used here.
target_link_libraries(missiondebug_capture PRIVATE ${rclcpp_LIBRARIES})
target_include_directories(missiondebug_capture PRIVATE ${rclcpp_INCLUDE_DIRS})

# Install the .so where it can be imported. Lands in
# install/missiondebug_capture/lib/ for a colcon build; for the agent .deb,
# packaging/build-capture-so.sh finds the built .so directly and copies it into
# the venv site-packages, so this install rule is only used by colcon.
install(TARGETS missiondebug_capture
  DESTINATION lib/${PROJECT_NAME})

# ament_package() only when building under ament (colcon). A plain cmake build
# (the .deb path) does not have it and does not need it.
if(ament_cmake_FOUND)
  ament_package()
endif()
