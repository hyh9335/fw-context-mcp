# C7 planted fixture — build pin (source-deterministic lineage D)
cmake_minimum_required(VERSION 3.20)
project(sensor_slice C)

set(CONFIG_SENSOR_ENABLE y)

add_library(sensor sensor_module.c)

target_compile_definitions(sensor PRIVATE
  CONFIG_SENSOR_ENABLE=1
)
