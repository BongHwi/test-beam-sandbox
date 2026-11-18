# SPDX-License-Identifier: LGPL-3.0-or-later
cmake_minimum_required(VERSION 3.24)
project(P0FileSink LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
find_package(Boost 1.85 REQUIRED COMPONENTS container program_options filesystem date_time regex iostreams thread)
find_package(FairLogger 2.3.0 REQUIRED)
find_package(FairMQ 1.10.0 REQUIRED)
find_package(ROOT 6.30 REQUIRED COMPONENTS RIO RHTTP Hist)
set(USER_SOURCE /opt/build/src/nestdaq-user-impl)
add_executable(FileSink
  ${USER_SOURCE}/FileSink.cxx
  ${USER_SOURCE}/utility/Stoi.cxx
  ${USER_SOURCE}/utility/FileUtil.cxx
  ${USER_SOURCE}/utility/program_options.cxx
  ${USER_SOURCE}/utility/TaskProcessorMT.cxx)
target_include_directories(FileSink PRIVATE ${USER_SOURCE} /opt/nestdaq/include /opt/nestdaq/include/fairlogger/bundled)
target_link_directories(FileSink PRIVATE /opt/nestdaq/lib)
target_link_libraries(FileSink PRIVATE FairMQ::FairMQ FairLogger::FairLogger Boost::program_options Boost::iostreams Boost::thread hiredis redis++ ROOT::RIO ROOT::RHTTP ROOT::Hist stdc++fs)
set_target_properties(FileSink PROPERTIES BUILD_RPATH "/opt/nestdaq/lib;/opt/conda/lib")
