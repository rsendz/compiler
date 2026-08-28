cmake_minimum_required(VERSION 3.16)

project(littleduck_vm LANGUAGES CXX)

add_executable(littleduck-vm src/main.cpp)
target_compile_features(littleduck-vm PRIVATE cxx_std_17)

if (MSVC)
    target_compile_options(littleduck-vm PRIVATE /W4 /permissive-)
else()
    target_compile_options(littleduck-vm PRIVATE -Wall -Wextra -Wpedantic)
endif()
