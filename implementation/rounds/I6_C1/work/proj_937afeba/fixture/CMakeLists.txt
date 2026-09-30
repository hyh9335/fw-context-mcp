# DERIVED_FROM: zephyrproject-rtos/zephyr tag v3.7.0, drivers/sensor/st/lis2dh/CMakeLists.txt
# (conditional trigger-source listing preserved; standalone slice for corpus case C1)
# SPDX-License-Identifier: Apache-2.0

cmake_minimum_required(VERSION 3.20)
project(accel_slice C)

set(CONFIG_ACCEL_SAMPLE y)
set(CONFIG_ACCEL_TRIGGER y)
set(CONFIG_ACCEL_TRIGGER_GLOBAL_THREAD y)

add_library(accel accel_core.c)

if(CONFIG_ACCEL_TRIGGER)
  target_sources(accel PRIVATE accel_trigger.c)
endif()

target_compile_definitions(accel PRIVATE
  CONFIG_ACCEL_SAMPLE=1
  CONFIG_ACCEL_TRIGGER=1
  CONFIG_ACCEL_TRIGGER_GLOBAL_THREAD=1
)
