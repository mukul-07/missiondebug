cmake_minimum_required(VERSION 3.8)
project(missiondebug_capture)

if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 17)
endif()

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
# pybind11's new tools call python3_add_library, which only exists after
# FindPython3 is loaded with the Development component. Find it first.
find_package(Python3 REQUIRED COMPONENTS Interpreter Development)
find_package(pybind11_vendor QUIET)
find_package(pybind11 REQUIRED)

# Build the Python extension module. pybind11_add_module produces a correctly
# named, suffixed shared object (missiondebug_capture.cpython-310-*.so).
pybind11_add_module(missiondebug_capture src/capture.cpp)
ament_target_dependencies(missiondebug_capture rclcpp)

# Install the .so where it can be imported. Lands in
# install/missiondebug_capture/lib/ for a colcon build; for the agent .deb we
# copy it next to the Python package. (Phase 1 just needs it importable for the
# parity test; final placement is a Phase 3 packaging concern.)
install(TARGETS missiondebug_capture
  DESTINATION lib/${PROJECT_NAME})

ament_package()
