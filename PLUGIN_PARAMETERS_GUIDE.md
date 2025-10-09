# Guide: Working with 3rd Party Plugin Parameters

## Overview

The MCP server now supports **full parameter access** for 3rd party VST/AU/AAX plugins. There are two approaches depending on your use case:

1. **Direct Parameter Access** - For simple plugins or when you need full parameter lists
2. **Rack + Macro Workflow** - For complex plugins with many parameters (recommended)

---

## Approach 1: Direct Parameter Access

### When to Use
- Plugin has < 50 parameters
- You need to access/modify many parameters programmatically
- Simple, one-off parameter changes

### How It Works
```python
# 1. Get track info to find the device index
get_track_info(track_index=0)

# 2. Get all parameters for the plugin
get_device_parameters(track_index=0, device_index=0)
# Returns:
# {
#   "device_name": "FabFilter Pro-Q 3",
#   "is_plugin": true,
#   "parameter_count": 87,
#   "parameters": [
#     {"index": 0, "name": "Output Gain", "value": 0.5, "min": 0.0, "max": 1.0},
#     {"index": 1, "name": "Band 1 Frequency", "value": 440.0, ...},
#     ...
#   ]
# }

# 3. Set a parameter by index or name
set_device_parameter(
    track_index=0,
    device_index=0,
    parameter_name="Output Gain",  # or use parameter_index=0
    value=0.75
)
```

### Improvements Made
The `get_device_parameters` function now:
- ✅ Detects 3rd party plugins automatically (`PluginDevice`, `AuPluginDevice`)
- ✅ Returns **ALL** parameters for plugins (no filtering)
- ✅ Includes `is_plugin` flag in response
- ✅ Shows `parameter_count` vs `total_parameters`

---

## Approach 2: Rack + Macro Workflow (Recommended)

### When to Use
- Plugin has 100+ parameters (e.g., Serum, Massive, etc.)
- You want to expose 8 or fewer key parameters
- Best practice for performance and organization

### Why This Approach?
- **Performance**: Only exposes the parameters you actually need
- **Organization**: Groups related parameters together
- **User-Friendly**: Macro names can be customized
- **Automation-Friendly**: Macros are easier to automate

### Complete Workflow

#### Step 1: Load Plugin into a Rack
```python
# Option A: Load instrument rack, then load plugin into it
# (You'll need to manually load the plugin into the rack in Ableton UI)

# Option B: If plugin is already on track, group it into a rack
# (Do this manually in Ableton: Right-click device → Group)
```

#### Step 2: Find the Plugin Inside the Rack
```python
# Get devices inside the rack's chain
get_rack_chain_devices(
    track_index=0,
    device_index=0,  # The rack device index
    chain_index=0    # Usually 0 for simple racks
)
# Returns:
# {
#   "rack_name": "Instrument Rack",
#   "devices": [
#     {"index": 0, "name": "FabFilter Pro-Q 3", "type": "audio_effect", ...}
#   ]
# }
```

#### Step 3: Get Plugin Parameters
```python
get_rack_chain_device_parameters(
    track_index=0,
    device_index=0,      # Rack index
    chain_index=0,       # Chain index (usually 0)
    chain_device_index=0 # Plugin index within chain
)
# Returns all parameters for the plugin inside the rack
```

#### Step 4: Map Parameters to Macros
```python
# Map up to 8 parameters to macro controls (0-7)
map_parameter_to_macro(
    track_index=0,
    device_index=0,          # Rack index
    chain_index=0,
    chain_device_index=0,    # Plugin index
    parameter_index=1,       # Plugin parameter index
    macro_index=0            # Macro 0-7
)

# Repeat for other parameters you want to expose
# macro_index 0 = Macro 1
# macro_index 1 = Macro 2
# ...
# macro_index 7 = Macro 8
```

#### Step 5: Control Plugin via Macros
```python
# Now control the plugin by setting the rack's macro parameters
set_device_parameter(
    track_index=0,
    device_index=0,        # Rack device index
    parameter_name="Macro 1",  # or use parameter_index
    value=0.8
)
```

#### Step 6: View Current Macro Mappings
```python
get_rack_macro_mappings(
    track_index=0,
    device_index=0  # Rack index
)
# Returns all 8 macros and what they're mapped to
```

---

## Comparison

| Feature | Direct Access | Rack + Macros |
|---------|--------------|---------------|
| Parameter Count | All parameters | 8 macros |
| Performance | Can be slow with 100+ params | Fast |
| Setup Complexity | Simple | Requires rack setup |
| Organization | Flat list | Organized groups |
| Best For | Simple plugins | Complex plugins |
| Automation | Direct | Via macros |

---

## Example: Complete Workflow with FabFilter Pro-Q 3

```python
# === Method 1: Direct Access ===
# Simple and straightforward
get_device_parameters(track_index=0, device_index=0)
set_device_parameter(track_index=0, device_index=0,
                    parameter_name="Output Gain", value=0.75)

# === Method 2: Rack + Macros ===
# More organized, better for complex plugins

# 1. Load FabFilter into a rack (do manually in Ableton)
# 2. Find plugin in rack
get_rack_chain_devices(track_index=0, device_index=0, chain_index=0)

# 3. Get plugin parameters to find what you want to map
get_rack_chain_device_parameters(
    track_index=0, device_index=0,
    chain_index=0, chain_device_index=0
)

# 4. Map important parameters to macros
# Map Output Gain → Macro 1
map_parameter_to_macro(
    track_index=0, device_index=0,
    chain_index=0, chain_device_index=0,
    parameter_index=0,  # Output Gain
    macro_index=0       # Macro 1
)

# 5. Control via macro (much simpler!)
set_device_parameter(track_index=0, device_index=0,
                    parameter_name="Macro 1", value=0.75)
```

---

## Troubleshooting

### "No parameters returned for my plugin"
- Make sure the plugin is fully loaded in Ableton
- Check that `is_plugin: true` in the response
- Try reloading the Ableton Remote Script

### "Parameter changes don't work"
- Verify parameter index/name is correct
- Check min/max range (value must be within bounds)
- For VST3, some parameters may be read-only

### "Macro mapping doesn't work"
- Device must be a Rack (Instrument/Audio/MIDI/Drum Rack)
- Macro index must be 0-7 (for 8 macros)
- Plugin must be inside the rack's chain

---

## API Reference

### Core Functions

| Function | Purpose |
|----------|---------|
| `get_device_parameters` | Get all parameters for any device/plugin |
| `set_device_parameter` | Set parameter by name or index |
| `get_rack_chain_devices` | List devices inside a rack |
| `get_rack_chain_device_parameters` | Get parameters from plugin in rack |
| `map_parameter_to_macro` | Map plugin parameter to rack macro |
| `get_rack_macro_mappings` | View current macro mappings |

### Parameter Object Structure

```json
{
  "index": 0,
  "name": "Output Gain",
  "value": 0.5,
  "min": 0.0,
  "max": 1.0,
  "is_enabled": true,
  "is_quantized": false
}
```

---

## Notes

- **3rd party plugins expose parameters through Ableton's API**, but the number can be overwhelming (100+ in some cases)
- **The rack workflow is Ableton's recommended approach** for managing plugin parameters
- **Parameter names come from the plugin manufacturer** and may vary between versions
- **Some plugins use quantized parameters** (discrete values) - check `is_quantized` field
