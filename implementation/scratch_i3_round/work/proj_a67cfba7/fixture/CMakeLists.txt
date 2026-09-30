cmake_minimum_required(VERSION 3.16)
project(pt_g7_handlers C)
add_library(pt_g7 STATIC handlers.c ctrl_set.c)
target_include_directories(pt_g7 PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
