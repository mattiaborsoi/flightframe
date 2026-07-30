#!/bin/sh
# SPDX-FileCopyrightText: 2026 YODE PTE LTD
# SPDX-License-Identifier: Apache-2.0
# Run the same checks CI runs, locally. From the firmware root:
#
#   tools/check.sh            # the six host tests (seconds)
#   tools/check.sh --build    # + the full ESP-IDF build via Docker
set -eu
cd "$(dirname "$0")/.."

for t in backoff api_base pairing_contract \
         provisioning_contract target_contract errlog_contract; do
    cc -Wall -Wextra -Werror "main/${t}.c" "tests/test_${t}.c" \
        -o "/tmp/fp_${t}"
    "/tmp/fp_${t}"
done

if [ "${1:-}" = "--build" ]; then
    docker run --rm -v "$PWD:/project" -w /project \
        espressif/idf:v5.3.1 idf.py -B build-check build
fi
echo "all checks pass"
