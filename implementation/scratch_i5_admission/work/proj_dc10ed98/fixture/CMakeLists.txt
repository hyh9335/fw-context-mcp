cmake_minimum_required(VERSION 3.16)
project(pt_g6_sensor C)
add_executable(pt_g6 main.c sensor_impl.c)
target_include_directories(pt_g6 PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
