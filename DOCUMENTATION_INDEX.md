# Documentation Index

Complete list of all markdown files in the MAVLink MCP project with descriptions.

**Last Updated:** December 2024

---

## Core Documentation

### README.md
**Main project documentation and getting started guide**
- Project overview and features
- Quick start instructions
- ChatGPT integration guide
- Setup and configuration
- Usage examples
- Troubleshooting basics
- Links to all other documentation

### STATUS.md
**Project status and development roadmap**
- Current version status (v1.2.4)
- Complete list of 35 available tools
- Development roadmap (v1.2.0, v1.3.0, v2.0.0, v3.0.0)
- Version comparison table
- Success metrics for each version
- Recent changes and release history
- Contributing guidelines

---

## Setup & Deployment Guides

### CHATGPT_SETUP.md
**Complete guide for connecting ChatGPT to MAVLink MCP**
- Prerequisites and requirements
- Step-by-step ChatGPT Developer Mode setup
- ngrok configuration for HTTPS tunneling
- MCP connector configuration in ChatGPT
- Testing and verification steps
- Troubleshooting common issues
- Example conversations and prompts

### SERVICE_SETUP.md
**Production deployment with systemd services**
- Installing MAVLink MCP as systemd service
- Installing ngrok as systemd service
- Auto-start on boot configuration
- Auto-restart on failure setup
- Centralized logging with journalctl
- Service management commands
- Production deployment best practices

### LIVE_SERVER_UPDATE.md
**Guide for updating running production server**
- Safe update procedures
- Stopping/restarting services
- Pulling latest code from GitHub
- Dependency updates
- Verifying updates
- Rollback procedures
- Zero-downtime update strategies

### RESTART_INSTRUCTIONS.md
**Quick reference for restarting services and viewing logs**
- Restart commands for MCP server
- Viewing live logs with journalctl
- Log filtering and searching
- Service status checks
- Troubleshooting service issues

---

## Reference Documentation

### MAVSDK_METHODS.md
**Complete MAVSDK Python API reference**
- All ~155 MAVSDK methods organized by plugin
- Implementation status for each method
- MCP tool mappings
- Coverage statistics (~23% implemented)
- Priority recommendations for future implementation

### MAVLINK_COMMANDS.md
**Complete MAVLink commands (MAV_CMD) reference**
- All ~120 MAVLink commands organized by category
- Implementation status for each command
- Direct vs indirect usage mapping
- MCP tool mappings
- Coverage statistics (~9% implemented)
- Priority recommendations

### MCP_TOOLS_MAVSDK.md
**MCP tools vs MAVSDK methods mapping**
- All 35 MCP tools listed
- Which tools are direct MAVSDK equivalents (23 tools)
- Which tools are custom implementations (13 tools)
- Which tools are partial equivalents (6 tools)
- Detailed explanations of custom logic
- Usage recommendations

---

## Testing Documentation

### TESTING.md
**Manual testing procedures using ChatGPT prompts**
- Note: Automated tests not implemented yet
- Quick test (5 minutes)
- Comprehensive test (15-20 minutes)
- Granular test (30-45 minutes)
- Individual feature tests
- Upload/download mission diagnostic
- Safety notes and prerequisites

### TESTING_REFERENCE.md
**Troubleshooting and testing reference guide**
- Common issues and solutions
- GPS coordinate calculations
- Firmware compatibility matrix
- Safety notes and best practices
- Test result templates
- Performance benchmarks
- Advanced troubleshooting

---

## Technical Documentation

### FLIGHT_MODES.md
**Flight mode behavior and MCP tool interactions**
- ArduPilot flight modes explained
- GUIDED mode (primary mode used)
- AUTO mode (mission execution)
- LOITER mode (problematic behavior)
- How MCP tools trigger mode changes
- Mode transition diagrams
- Best practices for mode management

### FLIGHT_LOGS.md
**Flight logging system documentation**
- Automatic flight log creation
- Log file location and naming
- Log entry formats
- MCP tool call logging
- MAVLink command logging
- Log analysis examples
- Using logs for debugging and auditing

### LOG_COLORS.md
**Color-coded logging system reference**
- ANSI color scheme for different log types
- MCP tool calls (green)
- MAVLink commands (cyan)
- Errors (red)
- Warnings (yellow)
- How to view colors in journalctl
- Log filtering examples

---

## Safety & Incident Reports

### LOITER_MODE_CRASH_REPORT.md
**Critical safety issue report - pause_mission crash**
- Executive summary of crash incident
- Detailed timeline of crash (25m → ground impact)
- Root cause analysis (LOITER mode altitude behavior)
- Why pause_mission is unsafe
- Safe alternative (hold_mission_position)
- Lessons learned
- Impact and fix status

### MISSION_PAUSE_FIX.md
**Mission pause/resume fixes and improvements**
- Issues addressed in v1.2.2
- LOITER mode problem explanation
- hold_mission_position solution
- Enhanced resume_mission diagnostics
- Migration guide from pause_mission
- Best practices for mission control

---

## Examples

### examples/README.md
**Example agent documentation**
- MCP and MAVSDK overview
- Prerequisites for running examples
- API key configuration
- Example agent usage
- FastAgent integration examples
- Code examples and snippets

---

## Summary by Category

### Setup & Getting Started (5 files)
- README.md - Main documentation
- CHATGPT_SETUP.md - ChatGPT integration
- SERVICE_SETUP.md - Production deployment
- LIVE_SERVER_UPDATE.md - Update procedures
- RESTART_INSTRUCTIONS.md - Quick restart guide

### Reference Documentation (3 files)
- MAVSDK_METHODS.md - MAVSDK API reference
- MAVLINK_COMMANDS.md - MAVLink commands reference
- MCP_TOOLS_MAVSDK.md - Tool mapping reference

### Status & Roadmap (1 file)
- STATUS.md - Project status and roadmap

### Testing (2 files)
- TESTING.md - Manual testing procedures
- TESTING_REFERENCE.md - Troubleshooting guide

### Technical Details (3 files)
- FLIGHT_MODES.md - Flight mode behavior
- FLIGHT_LOGS.md - Logging system
- LOG_COLORS.md - Color-coded logs

### Safety Reports (2 files)
- LOITER_MODE_CRASH_REPORT.md - Crash incident report
- MISSION_PAUSE_FIX.md - Mission control fixes

### Examples (1 file)
- examples/README.md - Example agent documentation

**Total: 17 markdown files**

---

## Quick Navigation

**New to the project?** Start with:
1. README.md - Overview and quick start
2. CHATGPT_SETUP.md - If using ChatGPT
3. SERVICE_SETUP.md - For production deployment

**Want to understand the codebase?** Read:
1. STATUS.md - Current features and roadmap
2. MCP_TOOLS_MAVSDK.md - What tools exist
3. MAVSDK_METHODS.md - What's available in MAVSDK

**Need to test?** See:
1. TESTING.md - Manual testing procedures
2. TESTING_REFERENCE.md - Troubleshooting

**Safety concerns?** Check:
1. LOITER_MODE_CRASH_REPORT.md - Critical safety issue
2. MISSION_PAUSE_FIX.md - Safe mission control

**Production deployment?** Read:
1. SERVICE_SETUP.md - Service installation
2. LIVE_SERVER_UPDATE.md - Update procedures
3. LOG_COLORS.md - Log viewing

---

## Documentation Maintenance

**Last Major Update:** December 2024
- Added MAVSDK_METHODS.md
- Added MAVLINK_COMMANDS.md
- Added MCP_TOOLS_MAVSDK.md
- Consolidated testing documentation into TESTING.md
- Updated STATUS.md with v1.3.0 roadmap

**Documentation Standards:**
- All guides include prerequisites
- Code examples are tested
- Safety warnings are prominent
- Links between related docs are maintained
