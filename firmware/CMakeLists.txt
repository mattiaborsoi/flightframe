cmake_minimum_required(VERSION 3.16)
# Single source of the firmware version; reported to the server as
# X-Fw-Version via the app descriptor (the OTA adoption signal).
#
# Bump this for every image that leaves the bench, and never reuse a number
# that has already been uploaded — not even to retry a release. A release
# owns its version on the server side, along with the adoption history
# recorded against it.
set(PROJECT_VER "1.0.1")
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(flightportrait)
