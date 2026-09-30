# Standalone slice for corpus case C5 (linkmon). SPDX-License-Identifier: Apache-2.0

cmake_minimum_required(VERSION 3.20)
project(linkmon_slice C)

add_library(linkmon linkmon.c)

target_compile_definitions(linkmon PRIVATE CONFIG_LINKMON_PM_GATE=1)
