# MAVLink MCP Testing Guide

Quick reference for testing v1.2.3+ features using ChatGPT natural language control.

## Prerequisites

Before testing, ensure:
1. ✅ MAVLink MCP server is running (`./start_http_server.sh`)
2. ✅ Drone/SITL is connected and GPS lock acquired
3. ✅ ChatGPT is connected to your MCP server via ngrok HTTPS
4. ✅ You're in an open, safe area for testing

---

## 📚 Test Suites

Choose the test that fits your needs:

### 🚀 [Quick Test (5 minutes)](TESTING_QUICK.md)
**Best for:** First-time setup verification or quick feature check
- 13 simple steps
- Tests basic flight + new v1.2.0 features
- Minimal complexity

### 🎯 [Comprehensive Test (15-20 minutes)](TESTING_COMPREHENSIVE.md)
**Best for:** Full system validation
- 7 phases, 30 operations
- Complete tower inspection scenario
- Tests all 36 tools in realistic workflow
- Includes detailed final report template

### 🔬 [Granular Test (30-45 minutes)](TESTING_GRANULAR.md)
**Best for:** Deep verification with ACK/NACK logic
- 44 individual tests with prerequisites
- Verifies drone *actually* performed each action
- Intelligent safety checks
- Production readiness assessment

### 🧪 [Individual Feature Tests](TESTING_INDIVIDUAL.md)
**Best for:** Debugging specific features or learning individual tools
- Parameter Management (3 tests)
- Advanced Navigation (3 tests)
- Mission Enhancements (2 tests)
- Isolated, repeatable tests

### 📖 [Reference Guide](TESTING_REFERENCE.md)
**Best for:** Troubleshooting and advanced topics
- Common issues & solutions
- Firmware compatibility matrix
- Safety notes
- Test results template
- Customization guide

---

## 🆕 What's New in v1.2.3

### 🔴 CRITICAL SAFETY UPDATE
- **`pause_mission()` DEPRECATED** - Enters LOITER mode which does NOT hold altitude
- **Use `hold_mission_position()` instead** - Safe GUIDED mode hold
- See [LOITER_MODE_CRASH_REPORT.md](LOITER_MODE_CRASH_REPORT.md) for details

### ✅ Enhanced Mission Control (v1.2.2)
- `resume_mission()` - Now verifies mode transition and reports waypoint progress
- `is_mission_finished()` - Returns detailed status including waypoint progress
- `hold_mission_position()` - NEW safe alternative to pause_mission

### ✅ Flight Logging
- Automatic logging of all tool calls and MAVLink commands
- Timestamped log files in `flight_logs/`
- See [FLIGHT_LOGS.md](FLIGHT_LOGS.md)

---

## 🎯 Quick Start

### Option 1: Quick Validation (5 min)
```bash
# Copy the prompt from TESTING_QUICK.md into ChatGPT
cat TESTING_QUICK.md
```

### Option 2: Full System Test (20 min)
```bash
# Copy the comprehensive test from TESTING_COMPREHENSIVE.md
cat TESTING_COMPREHENSIVE.md
```

### Option 3: Production Verification (45 min)
```bash
# Copy the granular test from TESTING_GRANULAR.md
cat TESTING_GRANULAR.md
```

---

## 📊 Feature Coverage

| Category | Tools | Quick Test | Comprehensive | Granular | Individual |
|----------|-------|------------|---------------|----------|------------|
| **Telemetry & Health** | 7 | ✅ | ✅ | ✅ (7 tests) | ⚠️ |
| **Parameter Management** | 3 | ✅ | ✅ | ✅ (4 tests) | ✅ (3 tests) |
| **Basic Flight** | 10 | ✅ | ✅ | ✅ (10 tests) | ⚠️ |
| **Advanced Navigation** | 3 | ✅ | ✅ | ✅ (9 tests) | ✅ (3 tests) |
| **Mission Management** | 10 | ✅ | ✅ | ✅ (11 tests) | ✅ (2 tests) |
| **Safety & Emergency** | 3 | ✅ | ✅ | ✅ (3 tests) | ⚠️ |
| **Total Tools** | **36** | 15/36 | 35/36 | 44 tests | 8 focused |

---

## 🚨 Safety Reminders

⚠️ **ALWAYS:**
- Test in open area away from people/buildings
- Maintain visual line of sight
- Have RC transmitter ready for manual override
- Monitor battery levels closely
- Start with low altitudes (5-10m)

⚠️ **NEVER:**
- Test near airports or restricted airspace
- Fly in bad weather (wind, rain, fog)
- Test with low battery
- Leave drone unattended
- Ignore safety warnings
- **Use `pause_mission()` - IT'S DEPRECATED AND UNSAFE**

---

## 🔧 Need Help?

**Problem with tests?** → See [TESTING_REFERENCE.md](TESTING_REFERENCE.md) for troubleshooting

**Mission upload errors?** → See [TESTING_FIXES.md](TESTING_FIXES.md) for format examples

**Pause/resume issues?** → See [MISSION_PAUSE_FIX.md](MISSION_PAUSE_FIX.md) for migration guide

**Loiter mode crash?** → See [LOITER_MODE_CRASH_REPORT.md](LOITER_MODE_CRASH_REPORT.md) for analysis

**General questions?** → See [README.md](README.md) or [STATUS.md](STATUS.md)

---

## 📈 Test Results

After running tests, record your results using the template in [TESTING_REFERENCE.md](TESTING_REFERENCE.md#test-results-template).

---

## 🤝 Contributing

Found issues or have suggestions?
1. Open an issue at: https://github.com/PeterJBurke/MAVLinkMCP/issues
2. Include your test results
3. Provide MCP server logs
4. Describe your setup (drone, autopilot, location)

---

**Happy Testing! 🚁✨**
