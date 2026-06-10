#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Run performance tests with regression detection

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
NC='\033[0m'

echo -e "${BLUE}════════════════════════════════════════════${NC}"
echo -e "${BLUE}   Performance Regression Testing${NC}"
echo -e "${BLUE}════════════════════════════════════════════${NC}"
echo ""

# Parse options
SAVE_BASELINE=false
FAIL_ON_REGRESSION=false
BASELINE_FILE=".performance_baseline.json"
SHOW_BASELINE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --save-baseline)
            SAVE_BASELINE=true
            shift
            ;;
        --fail-on-regression)
            FAIL_ON_REGRESSION=true
            shift
            ;;
        --baseline-file)
            BASELINE_FILE="$2"
            shift 2
            ;;
        --show-baseline)
            SHOW_BASELINE=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [options]"
            echo ""
            echo "Options:"
            echo "  --save-baseline           Save current results as new baseline"
            echo "  --fail-on-regression      Fail tests if regression detected (default: warn only)"
            echo "  --baseline-file FILE      Use specific baseline file"
            echo "  --show-baseline           Show current baseline and exit"
            echo "  -h, --help               Show this help"
            echo ""
            echo "Workflow:"
            echo "  1. First run:  $0 --save-baseline"
            echo "  2. After changes: $0"
            echo "  3. If regression: investigate and optimize"
            echo "  4. After optimization: $0 --save-baseline"
            echo ""
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

# Show baseline if requested
if [ "$SHOW_BASELINE" = true ]; then
    if [ -f "$BASELINE_FILE" ]; then
        echo -e "${BLUE}Current Performance Baseline:${NC}"
        echo ""
        python3 << EOF
import json
with open('$BASELINE_FILE', 'r') as f:
    baselines = json.load(f)
    for name, baseline in sorted(baselines.items()):
        print(f"  {name}:")
        print(f"    Mean: {baseline['mean_ms']:.2f} ms")
        print(f"    P95:  {baseline['p95_ms']:.2f} ms")
        print(f"    Recorded: {baseline['timestamp'][:19]}")
        print()
EOF
    else
        echo -e "${YELLOW}No baseline file found: $BASELINE_FILE${NC}"
        echo "Run with --save-baseline to create one."
    fi
    exit 0
fi

# Set environment variables for tests
export PERFORMANCE_BASELINE_FILE="$BASELINE_FILE"

if [ "$SAVE_BASELINE" = true ]; then
    export SAVE_PERFORMANCE_BASELINE=1
    echo -e "${MAGENTA}📝 Mode: SAVE BASELINE${NC}"
    echo -e "   Performance results will be saved as new baseline"
else
    export SAVE_PERFORMANCE_BASELINE=0
    if [ -f "$BASELINE_FILE" ]; then
        echo -e "${MAGENTA}📊 Mode: COMPARE WITH BASELINE${NC}"
        echo -e "   Comparing against: $BASELINE_FILE"
    else
        echo -e "${YELLOW}⚠️  No baseline found${NC}"
        echo -e "   Run with --save-baseline to establish baseline"
    fi
fi

if [ "$FAIL_ON_REGRESSION" = true ]; then
    export FAIL_ON_REGRESSION=1
    echo -e "${YELLOW}⚡ Fail-on-regression: ENABLED${NC}"
else
    export FAIL_ON_REGRESSION=0
    echo -e "${BLUE}ℹ️  Fail-on-regression: DISABLED (warnings only)${NC}"
fi

echo ""
echo -e "${YELLOW}Running performance tests...${NC}"
echo ""

# Run performance tests with detailed output
pytest tests/ \
    -m performance \
    -v \
    --tb=short \
    -s \
    --color=yes

TEST_EXIT_CODE=$?

echo ""
echo -e "${BLUE}════════════════════════════════════════════${NC}"

if [ $TEST_EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}✅ Performance tests completed successfully${NC}"

    if [ "$SAVE_BASELINE" = true ]; then
        echo -e "${GREEN}💾 Baseline saved to: $BASELINE_FILE${NC}"
        echo ""
        echo "Next steps:"
        echo "  • Commit baseline: git add $BASELINE_FILE"
        echo "  • Run tests: $0"
    fi
else
    if [ "$FAIL_ON_REGRESSION" = true ]; then
        echo -e "${RED}❌ Performance regression detected!${NC}"
    else
        echo -e "${YELLOW}⚠️  Performance tests had warnings${NC}"
    fi
fi

echo -e "${BLUE}════════════════════════════════════════════${NC}"
echo ""

# Show results summary
if [ -f "$BASELINE_FILE" ] && [ "$SAVE_BASELINE" = false ]; then
    echo -e "${BLUE}📊 Regression Analysis:${NC}"
    echo "  Check the output above for:"
    echo "  • 🎉 Improvements (functions got faster)"
    echo "  • ✅ Stable (within 20% of baseline)"
    echo "  • ⚠️  Regressions (functions got >20% slower)"
    echo ""
fi

# Performance tips
echo -e "${BLUE}💡 Commands:${NC}"
echo "  Show baseline:     $0 --show-baseline"
echo "  Update baseline:   $0 --save-baseline"
echo "  Fail on regression: $0 --fail-on-regression"
echo ""
echo "  Profile code:      ./scripts/profile_code.sh video_processor"
echo "  Quick tests:       ./scripts/test_quick.sh"
echo ""

exit $TEST_EXIT_CODE
