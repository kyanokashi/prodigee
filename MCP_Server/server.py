# ableton_mcp_server.py
from mcp.server.fastmcp import FastMCP, Context
import socket
import json
import logging
from dataclasses import dataclass
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Any, List, Union, Optional

# Configure logging
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("AbletonMCPServer")

@dataclass
class AbletonConnection:
    socket_path: str
    sock: socket.socket = None

    def connect(self) -> bool:
        """Connect to the Ableton Remote Script Unix domain socket server"""
        if self.sock:
            return True

        try:
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.connect(self.socket_path)
            logger.info(f"Connected to Ableton at {self.socket_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Ableton: {str(e)}")
            self.sock = None
            return False
    
    def disconnect(self):
        """Disconnect from the Ableton Remote Script"""
        if self.sock:
            try:
                self.sock.close()
            except Exception as e:
                logger.error(f"Error disconnecting from Ableton: {str(e)}")
            finally:
                self.sock = None

    def receive_full_response(self, sock, buffer_size=8192):
        """Receive the complete response, potentially in multiple chunks"""
        chunks = []
        sock.settimeout(15.0)  # Increased timeout for operations that might take longer
        
        try:
            while True:
                try:
                    chunk = sock.recv(buffer_size)
                    if not chunk:
                        if not chunks:
                            raise Exception("Connection closed before receiving any data")
                        break
                    
                    chunks.append(chunk)
                    
                    # Check if we've received a complete JSON object
                    try:
                        data = b''.join(chunks)
                        json.loads(data.decode('utf-8'))
                        logger.info(f"Received complete response ({len(data)} bytes)")
                        return data
                    except json.JSONDecodeError:
                        # Incomplete JSON, continue receiving
                        continue
                except socket.timeout:
                    logger.warning("Socket timeout during chunked receive")
                    break
                except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
                    logger.error(f"Socket connection error during receive: {str(e)}")
                    raise
        except Exception as e:
            logger.error(f"Error during receive: {str(e)}")
            raise
            
        # If we get here, we either timed out or broke out of the loop
        if chunks:
            data = b''.join(chunks)
            logger.info(f"Returning data after receive completion ({len(data)} bytes)")
            try:
                json.loads(data.decode('utf-8'))
                return data
            except json.JSONDecodeError:
                raise Exception("Incomplete JSON response received")
        else:
            raise Exception("No data received")

    def send_command(self, command_type: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Send a command to Ableton and return the response"""
        if not self.sock and not self.connect():
            raise ConnectionError("Not connected to Ableton")
        
        command = {
            "type": command_type,
            "params": params or {}
        }
        
        # Check if this is a state-modifying command
        is_modifying_command = command_type in [
            "create_midi_track", "create_audio_track", "set_track_name",
            "create_clip", "add_notes_to_clip", "add_new_notes_to_clip", "set_clip_name",
            "remove_notes_from_clip", "modify_notes_in_clip", "select_notes_from_clip",
            "set_track_volume", "set_track_pan", "set_track_mute", "set_track_solo", "set_track_arm",
            "delete_track", "duplicate_track", "delete_clip", "duplicate_clip",
            "set_clip_loop", "set_clip_color", "add_automation_point", "clear_automation",
            "create_scene", "delete_scene", "fire_scene",
            "set_loop_start", "set_loop_end", "set_playback_position", "set_metronome",
            "quantize_notes", "transpose_notes",
            "set_tempo", "fire_clip", "stop_clip", "set_device_parameter", "set_device_parameters",
            "start_playback", "stop_playback", "load_instrument_or_effect"
        ]
        
        try:
            logger.info(f"Sending command: {command_type} with params: {params}")
            
            # Send the command
            self.sock.sendall(json.dumps(command).encode('utf-8'))
            logger.info(f"Command sent, waiting for response...")
            
            # For state-modifying commands, add a small delay to give Ableton time to process
            if is_modifying_command:
                import time
                time.sleep(0.1)  # 100ms delay
            
            # Set timeout based on command type
            timeout = 15.0 if is_modifying_command else 10.0
            self.sock.settimeout(timeout)
            
            # Receive the response
            response_data = self.receive_full_response(self.sock)
            logger.info(f"Received {len(response_data)} bytes of data")
            
            # Parse the response
            response = json.loads(response_data.decode('utf-8'))
            logger.info(f"Response parsed, status: {response.get('status', 'unknown')}")
            
            if response.get("status") == "error":
                logger.error(f"Ableton error: {response.get('message')}")
                raise Exception(response.get("message", "Unknown error from Ableton"))
            
            # For state-modifying commands, add another small delay after receiving response
            if is_modifying_command:
                import time
                time.sleep(0.1)  # 100ms delay
            
            return response.get("result", {})
        except socket.timeout:
            logger.error("Socket timeout while waiting for response from Ableton")
            self.sock = None
            raise Exception("Timeout waiting for Ableton response")
        except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
            logger.error(f"Socket connection error: {str(e)}")
            self.sock = None
            raise Exception(f"Connection to Ableton lost: {str(e)}")
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response from Ableton: {str(e)}")
            if 'response_data' in locals() and response_data:
                logger.error(f"Raw response (first 200 bytes): {response_data[:200]}")
            self.sock = None
            raise Exception(f"Invalid response from Ableton: {str(e)}")
        except Exception as e:
            logger.error(f"Error communicating with Ableton: {str(e)}")
            self.sock = None
            raise Exception(f"Communication error with Ableton: {str(e)}")

@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    """Manage server startup and shutdown lifecycle"""
    try:
        logger.info("AbletonMCP server starting up")
        
        try:
            ableton = get_ableton_connection()
            logger.info("Successfully connected to Ableton on startup")
        except Exception as e:
            logger.warning(f"Could not connect to Ableton on startup: {str(e)}")
            logger.warning("Make sure the Ableton Remote Script is running")
        
        yield {}
    finally:
        global _ableton_connection
        if _ableton_connection:
            logger.info("Disconnecting from Ableton on shutdown")
            _ableton_connection.disconnect()
            _ableton_connection = None
        logger.info("AbletonMCP server shut down")

# Create the MCP server with lifespan support and default instructions
mcp = FastMCP(
    "AbletonMCP",
    lifespan=server_lifespan,
    instructions="""You are an expert Ableton Live music producer and audio engineer assistant. You have deep knowledge of:

- Music theory, composition, and arrangement
- MIDI programming and sequencing
- Sound design and synthesis
- Audio effects and mixing techniques
- Ableton Live's workflow and best practices

When working with Ableton:
1. Always get session info first to understand the current project state
2. Use descriptive track and clip names for organization
3. Consider musical context (key, tempo, genre) when making creative suggestions
4. Explain your creative decisions in musical terms
5. Think about the overall arrangement and how parts work together
6. When programming MIDI, use musically appropriate velocities, timing, and note patterns
7. Consider the genre and style when suggesting instruments, effects, and production techniques

Be creative, practical, and focused on helping users create great-sounding music in Ableton Live."""
)

# Global connection for resources
_ableton_connection = None

def get_ableton_connection():
    """Get or create a persistent Ableton connection"""
    global _ableton_connection
    
    if _ableton_connection is not None:
        try:
            # Test the connection with a simple ping
            # We'll try to send an empty message, which should fail if the connection is dead
            # but won't affect Ableton if it's alive
            _ableton_connection.sock.settimeout(1.0)
            _ableton_connection.sock.sendall(b'')
            return _ableton_connection
        except Exception as e:
            logger.warning(f"Existing connection is no longer valid: {str(e)}")
            try:
                _ableton_connection.disconnect()
            except:
                pass
            _ableton_connection = None
    
    # Connection doesn't exist or is invalid, create a new one
    if _ableton_connection is None:
        # Try to connect up to 3 times with a short delay between attempts
        max_attempts = 3
        socket_path = "/tmp/ableton_mcp.sock"
        for attempt in range(1, max_attempts + 1):
            try:
                logger.info(f"Connecting to Ableton (attempt {attempt}/{max_attempts})...")
                _ableton_connection = AbletonConnection(socket_path=socket_path)
                if _ableton_connection.connect():
                    logger.info("Created new persistent connection to Ableton")

                    # Validate connection with a simple command
                    try:
                        # Get session info as a test
                        _ableton_connection.send_command("get_session_info")
                        logger.info("Connection validated successfully")
                        return _ableton_connection
                    except Exception as e:
                        logger.error(f"Connection validation failed: {str(e)}")
                        _ableton_connection.disconnect()
                        _ableton_connection = None
                        # Continue to next attempt
                else:
                    _ableton_connection = None
            except Exception as e:
                logger.error(f"Connection attempt {attempt} failed: {str(e)}")
                if _ableton_connection:
                    _ableton_connection.disconnect()
                    _ableton_connection = None

            # Wait before trying again, but only if we have more attempts left
            if attempt < max_attempts:
                import time
                time.sleep(1.0)

        # If we get here, all connection attempts failed
        if _ableton_connection is None:
            logger.error("Failed to connect to Ableton after multiple attempts")
            raise Exception("Could not connect to Ableton. Make sure the Remote Script is running.")
    
    return _ableton_connection


# Prompts for specialized LLM behavior

@mcp.prompt()
def ableton_music_producer(ctx: Context) -> str:
    """System prompt for music production with Ableton Live"""
    return """You are an expert Ableton Live music producer and audio engineer. You have deep knowledge of:

- Music theory, composition, and arrangement
- MIDI programming and sequencing
- Sound design and synthesis
- Audio effects and mixing techniques
- Ableton Live's workflow and best practices

When working with Ableton:
1. Always get session info first to understand the current project state
2. Use descriptive track and clip names for organization
3. Consider musical context (key, tempo, genre) when making suggestions
4. Explain your creative decisions in musical terms
5. Think about the overall arrangement and how parts work together

Be creative, practical, and focused on helping create great-sounding music."""

@mcp.prompt()
def ableton_midi_programmer(ctx: Context) -> str:
    """System prompt specialized for MIDI programming"""
    return """You are a MIDI programming specialist for Ableton Live. Focus on:

- Creating musically interesting note patterns
- Using appropriate note velocities and timing
- Understanding scales, chords, and progressions
- Programming drums with realistic velocity and timing variations
- Creating melodies and basslines that work well together

Always consider the musical context and genre when programming MIDI."""


# Core Tool endpoints

@mcp.tool()
def get_session_info(ctx: Context) -> str:
    """Get detailed information about the current Ableton session"""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_session_info")
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting session info from Ableton: {str(e)}")
        return f"Error getting session info: {str(e)}"

@mcp.tool()
def get_track_info(ctx: Context, track_index: int) -> str:
    """
    Get detailed information about a specific track in Ableton.
    
    Parameters:
    - track_index: The index of the track to get information about
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_track_info", {"track_index": track_index})
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting track info from Ableton: {str(e)}")
        return f"Error getting track info: {str(e)}"

@mcp.tool()
def create_midi_track(ctx: Context, index: int = -1) -> str:
    """
    Create a new MIDI track in the Ableton session.
    
    Parameters:
    - index: The index to insert the track at (-1 = end of list)
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("create_midi_track", {"index": index})
        return f"Created new MIDI track: {result.get('name', 'unknown')}"
    except Exception as e:
        logger.error(f"Error creating MIDI track: {str(e)}")
        return f"Error creating MIDI track: {str(e)}"


@mcp.tool()
def set_track_name(ctx: Context, track_index: int, name: str) -> str:
    """
    Set the name of a track.
    
    Parameters:
    - track_index: The index of the track to rename
    - name: The new name for the track
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("set_track_name", {"track_index": track_index, "name": name})
        return f"Renamed track to: {result.get('name', name)}"
    except Exception as e:
        logger.error(f"Error setting track name: {str(e)}")
        return f"Error setting track name: {str(e)}"

@mcp.tool()
def create_clip(ctx: Context, track_index: int, clip_index: int, length: float = 4.0) -> str:
    """
    Create a new MIDI clip in the specified track and clip slot.
    
    Parameters:
    - track_index: The index of the track to create the clip in
    - clip_index: The index of the clip slot to create the clip in
    - length: The length of the clip in beats (default: 4.0)
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("create_clip", {
            "track_index": track_index, 
            "clip_index": clip_index, 
            "length": length
        })
        return f"Created new clip at track {track_index}, slot {clip_index} with length {length} beats"
    except Exception as e:
        logger.error(f"Error creating clip: {str(e)}")
        return f"Error creating clip: {str(e)}"

@mcp.tool()
def get_notes_from_clip(ctx: Context, track_index: int, clip_index: int) -> str:
    """
    Get MIDI notes from a clip using get_notes_extended.
    Returns note data including note IDs and all MIDI properties (MPE, probability, velocity deviation, etc.)

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_notes_from_clip", {
            "track_index": track_index,
            "clip_index": clip_index
        })
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting notes from clip: {str(e)}")
        return f"Error getting notes from clip: {str(e)}"

@mcp.tool()
def add_notes_to_clip(
    ctx: Context,
    track_index: int,
    clip_index: int,
    notes: List[Dict[str, Union[int, float, bool]]]
) -> str:
    """
    Add MIDI notes to a clip (REPLACES all existing notes - legacy method).
    Use add_new_notes_to_clip instead to add notes without replacing.

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    - notes: List of note dictionaries, each with pitch, start_time, duration, velocity, and mute
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("add_notes_to_clip", {
            "track_index": track_index,
            "clip_index": clip_index,
            "notes": notes
        })
        return f"Added {len(notes)} notes to clip at track {track_index}, slot {clip_index} (replaced existing notes)"
    except Exception as e:
        logger.error(f"Error adding notes to clip: {str(e)}")
        return f"Error adding notes to clip: {str(e)}"

@mcp.tool()
def add_new_notes_to_clip(
    ctx: Context,
    track_index: int,
    clip_index: int,
    notes: List[Dict[str, Union[int, float, bool]]]
) -> str:
    """
    Add new MIDI notes to a clip WITHOUT replacing existing notes (Live 11+).
    Supports extended properties: velocity_deviation, release_velocity, probability.

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    - notes: List of note dictionaries with properties:
      * Required: pitch, start_time, duration, velocity
      * Optional: mute, velocity_deviation, release_velocity, probability
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("add_new_notes_to_clip", {
            "track_index": track_index,
            "clip_index": clip_index,
            "notes": notes
        })
        return f"Added {len(notes)} new notes to clip at track {track_index}, slot {clip_index} (kept existing notes)"
    except Exception as e:
        logger.error(f"Error adding new notes to clip: {str(e)}")
        return f"Error adding new notes to clip: {str(e)}"

@mcp.tool()
def set_clip_name(ctx: Context, track_index: int, clip_index: int, name: str) -> str:
    """
    Set the name of a clip.
    
    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    - name: The new name for the clip
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("set_clip_name", {
            "track_index": track_index,
            "clip_index": clip_index,
            "name": name
        })
        return f"Renamed clip at track {track_index}, slot {clip_index} to '{name}'"
    except Exception as e:
        logger.error(f"Error setting clip name: {str(e)}")
        return f"Error setting clip name: {str(e)}"

@mcp.tool()
def set_tempo(ctx: Context, tempo: float) -> str:
    """
    Set the tempo of the Ableton session.
    
    Parameters:
    - tempo: The new tempo in BPM
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("set_tempo", {"tempo": tempo})
        return f"Set tempo to {tempo} BPM"
    except Exception as e:
        logger.error(f"Error setting tempo: {str(e)}")
        return f"Error setting tempo: {str(e)}"


@mcp.tool()
def load_instrument_or_effect(ctx: Context, track_index: int, uri: str) -> str:
    """
    Load an instrument or effect onto a track using its URI.
    
    Parameters:
    - track_index: The index of the track to load the instrument on
    - uri: The URI of the instrument or effect to load (e.g., 'query:Synths#Instrument%20Rack:Bass:FileId_5116')
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("load_browser_item", {
            "track_index": track_index,
            "item_uri": uri
        })
        
        # Check if the instrument was loaded successfully
        if result.get("loaded", False):
            new_devices = result.get("new_devices", [])
            if new_devices:
                return f"Loaded instrument with URI '{uri}' on track {track_index}. New devices: {', '.join(new_devices)}"
            else:
                devices = result.get("devices_after", [])
                return f"Loaded instrument with URI '{uri}' on track {track_index}. Devices on track: {', '.join(devices)}"
        else:
            return f"Failed to load instrument with URI '{uri}'"
    except Exception as e:
        logger.error(f"Error loading instrument by URI: {str(e)}")
        return f"Error loading instrument by URI: {str(e)}"

@mcp.tool()
def fire_clip(ctx: Context, track_index: int, clip_index: int) -> str:
    """
    Start playing a clip.
    
    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("fire_clip", {
            "track_index": track_index,
            "clip_index": clip_index
        })
        return f"Started playing clip at track {track_index}, slot {clip_index}"
    except Exception as e:
        logger.error(f"Error firing clip: {str(e)}")
        return f"Error firing clip: {str(e)}"

@mcp.tool()
def stop_clip(ctx: Context, track_index: int, clip_index: int) -> str:
    """
    Stop playing a clip.
    
    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("stop_clip", {
            "track_index": track_index,
            "clip_index": clip_index
        })
        return f"Stopped clip at track {track_index}, slot {clip_index}"
    except Exception as e:
        logger.error(f"Error stopping clip: {str(e)}")
        return f"Error stopping clip: {str(e)}"

@mcp.tool()
def start_playback(ctx: Context) -> str:
    """Start playing the Ableton session."""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("start_playback")
        return "Started playback"
    except Exception as e:
        logger.error(f"Error starting playback: {str(e)}")
        return f"Error starting playback: {str(e)}"

@mcp.tool()
def stop_playback(ctx: Context) -> str:
    """Stop playing the Ableton session."""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("stop_playback")
        return "Stopped playback"
    except Exception as e:
        logger.error(f"Error stopping playback: {str(e)}")
        return f"Error stopping playback: {str(e)}"

@mcp.tool()
def get_browser_tree(ctx: Context, category_type: str = "all") -> str:
    """
    Get a hierarchical tree of browser categories from Ableton.
    
    Parameters:
    - category_type: Type of categories to get ('all', 'instruments', 'sounds', 'drums', 'audio_effects', 'midi_effects')
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_browser_tree", {
            "category_type": category_type
        })
        
        # Check if we got any categories
        if "available_categories" in result and len(result.get("categories", [])) == 0:
            available_cats = result.get("available_categories", [])
            return (f"No categories found for '{category_type}'. "
                   f"Available browser categories: {', '.join(available_cats)}")
        
        # Format the tree in a more readable way
        total_folders = result.get("total_folders", 0)
        formatted_output = f"Browser tree for '{category_type}' (showing {total_folders} folders):\n\n"
        
        def format_tree(item, indent=0):
            output = ""
            if item:
                prefix = "  " * indent
                name = item.get("name", "Unknown")
                path = item.get("path", "")
                has_more = item.get("has_more", False)
                
                # Add this item
                output += f"{prefix}• {name}"
                if path:
                    output += f" (path: {path})"
                if has_more:
                    output += " [...]"
                output += "\n"
                
                # Add children
                for child in item.get("children", []):
                    output += format_tree(child, indent + 1)
            return output
        
        # Format each category
        for category in result.get("categories", []):
            formatted_output += format_tree(category)
            formatted_output += "\n"
        
        return formatted_output
    except Exception as e:
        error_msg = str(e)
        if "Browser is not available" in error_msg:
            logger.error(f"Browser is not available in Ableton: {error_msg}")
            return f"Error: The Ableton browser is not available. Make sure Ableton Live is fully loaded and try again."
        elif "Could not access Live application" in error_msg:
            logger.error(f"Could not access Live application: {error_msg}")
            return f"Error: Could not access the Ableton Live application. Make sure Ableton Live is running and the Remote Script is loaded."
        else:
            logger.error(f"Error getting browser tree: {error_msg}")
            return f"Error getting browser tree: {error_msg}"

@mcp.tool()
def get_browser_items_at_path(ctx: Context, path: str) -> str:
    """
    Get browser items at a specific path in Ableton's browser.
    
    Parameters:
    - path: Path in the format "category/folder/subfolder"
            where category is one of the available browser categories in Ableton
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_browser_items_at_path", {
            "path": path
        })
        
        # Check if there was an error with available categories
        if "error" in result and "available_categories" in result:
            error = result.get("error", "")
            available_cats = result.get("available_categories", [])
            return (f"Error: {error}\n"
                   f"Available browser categories: {', '.join(available_cats)}")
        
        return json.dumps(result, indent=2)
    except Exception as e:
        error_msg = str(eN)
        if "Browser is not available" in error_msg:
            logger.error(f"Browser is not available in Ableton: {error_msg}")
            return f"Error: The Ableton browser is not available. Make sure Ableton Live is fully loaded and try again."
        elif "Could not access Live application" in error_msg:
            logger.error(f"Could not access Live application: {error_msg}")
            return f"Error: Could not access the Ableton Live application. Make sure Ableton Live is running and the Remote Script is loaded."
        elif "Unknown or unavailable category" in error_msg:
            logger.error(f"Invalid browser category: {error_msg}")
            return f"Error: {error_msg}. Please check the available categories using get_browser_tree."
        elif "Path part" in error_msg and "not found" in error_msg:
            logger.error(f"Path not found: {error_msg}")
            return f"Error: {error_msg}. Please check the path and try again."
        else:
            logger.error(f"Error getting browser items at path: {error_msg}")
            return f"Error getting browser items at path: {error_msg}"

@mcp.tool()
def load_drum_kit(ctx: Context, track_index: int, rack_uri: str, kit_path: str) -> str:
    """
    Load a drum rack and then load a specific drum kit into it.
    
    Parameters:
    - track_index: The index of the track to load on
    - rack_uri: The URI of the drum rack to load (e.g., 'Drums/Drum Rack')
    - kit_path: Path to the drum kit inside the browser (e.g., 'drums/acoustic/kit1')
    """
    try:
        ableton = get_ableton_connection()
        
        # Step 1: Load the drum rack
        result = ableton.send_command("load_browser_item", {
            "track_index": track_index,
            "item_uri": rack_uri
        })
        
        if not result.get("loaded", False):
            return f"Failed to load drum rack with URI '{rack_uri}'"
        
        # Step 2: Get the drum kit items at the specified path
        kit_result = ableton.send_command("get_browser_items_at_path", {
            "path": kit_path
        })
        
        if "error" in kit_result:
            return f"Loaded drum rack but failed to find drum kit: {kit_result.get('error')}"
        
        # Step 3: Find a loadable drum kit
        kit_items = kit_result.get("items", [])
        loadable_kits = [item for item in kit_items if item.get("is_loadable", False)]
        
        if not loadable_kits:
            return f"Loaded drum rack but no loadable drum kits found at '{kit_path}'"
        
        # Step 4: Load the first loadable kit
        kit_uri = loadable_kits[0].get("uri")
        load_result = ableton.send_command("load_browser_item", {
            "track_index": track_index,
            "item_uri": kit_uri
        })
        
        return f"Loaded drum rack and kit '{loadable_kits[0].get('name')}' on track {track_index}"
    except Exception as e:
        logger.error(f"Error loading drum kit: {str(e)}")
        return f"Error loading drum kit: {str(e)}"

@mcp.tool()
def get_device_parameters(ctx: Context, track_index: int, device_index: int) -> str:
    """
    Get all parameters for a device (including 3rd party plugins).

    This tool works for both Ableton native devices and 3rd party VST/AU/AAX plugins.
    For 3rd party plugins, ALL parameters are returned without filtering.

    Parameters:
    - track_index: The index of the track containing the device
    - device_index: The index of the device on the track

    Returns:
    - JSON string with device information including:
      * device_name: Name of the device
      * device_class: Ableton's internal class name
      * device_type: Detected type (instrument, audio_effect, etc.)
      * is_plugin: True if this is a 3rd party plugin (VST/AU/AAX)
      * parameter_count: Number of accessible parameters
      * parameters: Array of parameter objects with index, name, value, min, max

    Note: For 3rd party plugins with many parameters (e.g., 100+), consider using
    the rack workflow: load plugin into a rack, map desired parameters to macros (0-7),
    then control via macros using set_device_parameter on the rack.
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_device_parameters", {
            "track_index": track_index,
            "device_index": device_index
        })

        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting device parameters: {str(e)}")
        return f"Error getting device parameters: {str(e)}"

@mcp.tool()
def set_device_parameter(ctx: Context, track_index: int, device_index: int,
                         parameter_name: Optional[str] = None,
                         parameter_index: Optional[int] = None,
                         value: Optional[Union[float, int, str]] = None,
                         parameters: Optional[List[Dict[str, Union[str, int, float]]]] = None) -> str:
    """
    Set one or multiple device parameters.

    For single parameter:
    - track_index: The index of the track containing the device
    - device_index: The index of the device on the track
    - parameter_name: The name of the parameter to set (alternative to parameter_index)
    - parameter_index: The index of the parameter to set (alternative to parameter_name)
    - value: The value to set the parameter to

    For multiple parameters:
    - track_index: The index of the track containing the device
    - device_index: The index of the device on the track
    - parameters: List of parameter dictionaries, each containing:
      * Either 'parameter_name' (str) or 'parameter_index' (int)
      * 'value' (float, int, or str)
      Example: [{"parameter_name": "Frequency", "value": 440}, {"parameter_index": 5, "value": 0.5}]

    Returns:
    - String with the result of the operation
    """
    try:
        # Check if we're setting multiple parameters
        if parameters is not None:
            if parameter_name is not None or parameter_index is not None or value is not None:
                return "Error: Cannot use both single parameter arguments and parameters list"

            if not isinstance(parameters, list) or len(parameters) == 0:
                return "Error: parameters must be a non-empty list"

            ableton = get_ableton_connection()
            result = ableton.send_command("set_device_parameters", {
                "track_index": track_index,
                "device_index": device_index,
                "parameters": parameters
            })

            if "results" in result:
                success_count = sum(1 for r in result["results"] if r.get("success", False))
                total_count = len(result["results"])
                summary = f"Set {success_count}/{total_count} parameters on device '{result['device_name']}':\n"
                for r in result["results"]:
                    if r.get("success"):
                        summary += f"  ✓ {r['parameter_name']}: {r['value']}\n"
                    else:
                        summary += f"  ✗ {r.get('parameter_name', 'unknown')}: {r.get('error', 'unknown error')}\n"
                return summary.rstrip()
            else:
                return f"Failed to set parameters: {result.get('message', 'Unknown error')}"
        else:
            # Single parameter mode
            if parameter_name is None and parameter_index is None:
                return "Error: Either parameter_name, parameter_index, or parameters list must be provided"

            if value is None:
                return "Error: Value must be provided for single parameter mode"

            ableton = get_ableton_connection()
            result = ableton.send_command("set_device_parameter", {
                "track_index": track_index,
                "device_index": device_index,
                "parameter_name": parameter_name,
                "parameter_index": parameter_index,
                "value": value
            })

            if "parameter_name" in result:
                return f"Set parameter '{result['parameter_name']}' of device '{result['device_name']}' to {result['value']}"
            else:
                return f"Failed to set parameter: {result.get('message', 'Unknown error')}"
    except Exception as e:
        logger.error(f"Error setting device parameter(s): {str(e)}")
        return f"Error setting device parameter(s): {str(e)}"

# ============================================================================
# MACRO CONTROL TOOLS
# ============================================================================

@mcp.tool()
def get_rack_chain_devices(ctx: Context, track_index: int, device_index: int, chain_index: int = 0) -> str:
    """
    Get all devices inside a rack's chain.

    Parameters:
    - track_index: The index of the track containing the rack
    - device_index: The index of the rack device
    - chain_index: The index of the chain (default 0 for single-chain racks)

    Returns:
    - JSON string with list of devices inside the rack's chain

    Use this to discover what devices (like plugins) are inside a rack.
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_rack_chain_devices", {
            "track_index": track_index,
            "device_index": device_index,
            "chain_index": chain_index
        })

        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting rack chain devices: {str(e)}")
        return f"Error getting rack chain devices: {str(e)}"

@mcp.tool()
def get_rack_chain_device_parameters(
    ctx: Context,
    track_index: int,
    device_index: int,
    chain_index: int,
    chain_device_index: int
) -> str:
    """
    Get parameters from a device inside a rack's chain.

    Parameters:
    - track_index: The index of the track containing the rack
    - device_index: The index of the rack device
    - chain_index: The index of the chain (usually 0)
    - chain_device_index: The index of the device inside the chain

    Returns:
    - JSON string with all parameters for the device inside the rack

    Use this to get parameters from 3rd party plugins inside racks.
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_rack_chain_device_parameters", {
            "track_index": track_index,
            "device_index": device_index,
            "chain_index": chain_index,
            "chain_device_index": chain_device_index
        })

        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting rack chain device parameters: {str(e)}")
        return f"Error getting rack chain device parameters: {str(e)}"

@mcp.tool()
def map_parameter_to_macro(
    ctx: Context,
    track_index: int,
    device_index: int,
    chain_index: int,
    chain_device_index: int,
    parameter_index: int,
    macro_index: int
) -> str:
    """
    Map a device parameter to a macro control in a Device Rack.
    This is the recommended way to control 3rd party plugin parameters.

    Parameters:
    - track_index: The index of the track containing the rack
    - device_index: The index of the rack device on the track
    - chain_index: The index of the chain (usually 0 for single-chain racks)
    - chain_device_index: The index of the device inside the chain
    - parameter_index: The index of the parameter on the device inside the chain
    - macro_index: The index of the macro control (0-7 for standard racks)

    Workflow:
    1. Use get_rack_chain_devices to find the device inside the rack
    2. Use get_rack_chain_device_parameters to see available parameters
    3. Use this tool to map parameters to macro controls (0-7)
    4. Control the plugin via macros using set_device_parameter on the rack's macro parameters

    Note: The device at device_index must be a Rack (Instrument/Audio/MIDI/Drum Rack).

    Returns:
    - JSON string with mapping information
    """
    try:
        if macro_index < 0 or macro_index > 7:
            return "Error: macro_index must be between 0 and 7"

        ableton = get_ableton_connection()
        result = ableton.send_command("map_parameter_to_macro", {
            "track_index": track_index,
            "device_index": device_index,
            "chain_index": chain_index,
            "chain_device_index": chain_device_index,
            "parameter_index": parameter_index,
            "macro_index": macro_index
        })

        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error mapping parameter to macro: {str(e)}")
        return f"Error mapping parameter to macro: {str(e)}"

@mcp.tool()
def get_rack_macro_mappings(ctx: Context, track_index: int, device_index: int) -> str:
    """
    Get all macro mappings for a Device Rack.

    Parameters:
    - track_index: The index of the track containing the rack
    - device_index: The index of the rack device

    Returns:
    - JSON string with information about each macro (0-7) and their current values

    Use this to see what macros are available in a rack device.
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_rack_macro_mappings", {
            "track_index": track_index,
            "device_index": device_index
        })

        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting rack macro mappings: {str(e)}")
        return f"Error getting rack macro mappings: {str(e)}"

# ============================================================================
# NOTE MANIPULATION TOOLS
# ============================================================================

@mcp.tool()
def remove_notes_from_clip(
    ctx: Context,
    track_index: int,
    clip_index: int,
    note_ids: Optional[List[int]] = None,
    from_time: Optional[float] = None,
    to_time: Optional[float] = None,
    from_pitch: Optional[int] = None,
    to_pitch: Optional[int] = None
) -> str:
    """
    Remove notes from a clip by note IDs or time/pitch range.

    Parameters:
    - track_index: Track index
    - clip_index: Clip slot index
    - note_ids: Optional list of note IDs to remove (if provided, range params ignored)
    - from_time: Start time in beats (for range removal)
    - to_time: End time in beats (for range removal)
    - from_pitch: Start pitch 0-127 (for range removal)
    - to_pitch: End pitch 0-127 (for range removal)
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("remove_notes_from_clip", {
            "track_index": track_index,
            "clip_index": clip_index,
            "note_ids": note_ids,
            "from_time": from_time,
            "to_time": to_time,
            "from_pitch": from_pitch,
            "to_pitch": to_pitch
        })
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error removing notes: {str(e)}")
        return f"Error removing notes: {str(e)}"

@mcp.tool()
def modify_notes_in_clip(
    ctx: Context,
    track_index: int,
    clip_index: int,
    modifications: List[Dict[str, Union[int, float, bool]]]
) -> str:
    """
    Modify existing notes in a clip by note_id (Live 11+).
    Each modification must include note_id and the properties to change.

    Parameters:
    - track_index: Track index
    - clip_index: Clip slot index
    - modifications: List of dicts with note_id and properties to modify
      (e.g., [{"note_id": 123, "pitch": 60, "velocity": 100}])
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("modify_notes_in_clip", {
            "track_index": track_index,
            "clip_index": clip_index,
            "modifications": modifications
        })
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error modifying notes: {str(e)}")
        return f"Error modifying notes: {str(e)}"

@mcp.tool()
def select_notes_from_clip(
    ctx: Context,
    track_index: int,
    clip_index: int,
    from_time: float = 0.0,
    to_time: Optional[float] = None,
    from_pitch: int = 0,
    to_pitch: int = 127
) -> str:
    """
    Select/filter notes from a clip by time and pitch range.

    Parameters:
    - track_index: Track index
    - clip_index: Clip slot index
    - from_time: Start time in beats
    - to_time: End time in beats (None = clip length)
    - from_pitch: Start pitch 0-127
    - to_pitch: End pitch 0-127
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("select_notes_from_clip", {
            "track_index": track_index,
            "clip_index": clip_index,
            "from_time": from_time,
            "to_time": to_time,
            "from_pitch": from_pitch,
            "to_pitch": to_pitch
        })
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error selecting notes: {str(e)}")
        return f"Error selecting notes: {str(e)}"

# ============================================================================
# TRACK & MIXER CONTROL TOOLS
# ============================================================================

@mcp.tool()
def set_track_volume(ctx: Context, track_index: int, volume: float) -> str:
    """Set track volume (0.0 to 1.0, where 0.85 ≈ 0dB)"""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("set_track_volume", {"track_index": track_index, "volume": volume})
        return f"Set track {track_index} volume to {volume}"
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def set_track_pan(ctx: Context, track_index: int, pan: float) -> str:
    """Set track pan (-1.0 = left, 0.0 = center, 1.0 = right)"""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("set_track_pan", {"track_index": track_index, "pan": pan})
        return f"Set track {track_index} pan to {pan}"
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def set_track_mute(ctx: Context, track_index: int, mute: bool) -> str:
    """Set track mute state"""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("set_track_mute", {"track_index": track_index, "mute": mute})
        return f"Set track {track_index} mute to {mute}"
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def set_track_solo(ctx: Context, track_index: int, solo: bool) -> str:
    """Set track solo state"""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("set_track_solo", {"track_index": track_index, "solo": solo})
        return f"Set track {track_index} solo to {solo}"
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def set_track_arm(ctx: Context, track_index: int, arm: bool) -> str:
    """Set track arm/record enable state"""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("set_track_arm", {"track_index": track_index, "arm": arm})
        return f"Set track {track_index} arm to {arm}"
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def delete_track(ctx: Context, track_index: int) -> str:
    """Delete a track"""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("delete_track", {"track_index": track_index})
        return f"Deleted track {track_index}"
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def duplicate_track(ctx: Context, track_index: int) -> str:
    """Duplicate a track"""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("duplicate_track", {"track_index": track_index})
        return f"Duplicated track {track_index} to index {result.get('new_track_index')}"
    except Exception as e:
        return f"Error: {str(e)}"

# ============================================================================
# CLIP CONTROL TOOLS
# ============================================================================

@mcp.tool()
def get_clip_info(ctx: Context, track_index: int, clip_index: int) -> str:
    """Get detailed information about a clip"""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_clip_info", {"track_index": track_index, "clip_index": clip_index})
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def delete_clip(ctx: Context, track_index: int, clip_index: int) -> str:
    """Delete a clip"""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("delete_clip", {"track_index": track_index, "clip_index": clip_index})
        return f"Deleted clip at track {track_index}, slot {clip_index}"
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def duplicate_clip(ctx: Context, track_index: int, clip_index: int) -> str:
    """Duplicate a clip to the next available slot"""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("duplicate_clip", {"track_index": track_index, "clip_index": clip_index})
        return f"Duplicated clip to slot {result.get('target_clip_index')}"
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def set_clip_loop(ctx: Context, track_index: int, clip_index: int, loop_start: float, loop_end: Optional[float] = None, loop_enabled: bool = True) -> str:
    """Set clip loop parameters"""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("set_clip_loop", {
            "track_index": track_index,
            "clip_index": clip_index,
            "loop_start": loop_start,
            "loop_end": loop_end,
            "loop_enabled": loop_enabled
        })
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def set_clip_color(ctx: Context, track_index: int, clip_index: int, color: int) -> str:
    """Set clip color (color index 0-69)"""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("set_clip_color", {"track_index": track_index, "clip_index": clip_index, "color": color})
        return f"Set clip color to {color}"
    except Exception as e:
        return f"Error: {str(e)}"

# ============================================================================
# AUTOMATION TOOLS
# ============================================================================

@mcp.tool()
def add_automation_point(ctx: Context, track_index: int, device_index: int, parameter_index: int, time: float, value: float) -> str:
    """Add an automation point to a parameter"""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("add_automation_point", {
            "track_index": track_index,
            "device_index": device_index,
            "parameter_index": parameter_index,
            "time": time,
            "value": value
        })
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def clear_automation(ctx: Context, track_index: int, device_index: int, parameter_index: int) -> str:
    """Clear automation for a parameter"""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("clear_automation", {
            "track_index": track_index,
            "device_index": device_index,
            "parameter_index": parameter_index
        })
        return f"Cleared automation for parameter {parameter_index}"
    except Exception as e:
        return f"Error: {str(e)}"

# ============================================================================
# SCENE CONTROL TOOLS
# ============================================================================

@mcp.tool()
def get_scenes_info(ctx: Context) -> str:
    """Get information about all scenes"""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_scenes_info")
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def create_scene(ctx: Context, index: int = -1) -> str:
    """Create a new scene at index (-1 = end)"""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("create_scene", {"index": index})
        return f"Created scene at index {result.get('scene_index')}"
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def delete_scene(ctx: Context, index: int) -> str:
    """Delete a scene"""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("delete_scene", {"index": index})
        return f"Deleted scene {index}"
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def fire_scene(ctx: Context, index: int) -> str:
    """Fire/trigger a scene"""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("fire_scene", {"index": index})
        return f"Fired scene {index}"
    except Exception as e:
        return f"Error: {str(e)}"

# ============================================================================
# TRANSPORT & TIMING TOOLS
# ============================================================================

@mcp.tool()
def get_playback_position(ctx: Context) -> str:
    """Get current playback position and loop state"""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_playback_position")
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def set_loop_start(ctx: Context, position: float) -> str:
    """Set arrangement loop start position (in beats)"""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("set_loop_start", {"position": position})
        return f"Set loop start to {position}"
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def set_loop_end(ctx: Context, position: float) -> str:
    """Set arrangement loop end position (in beats)"""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("set_loop_end", {"position": position})
        return f"Set loop end to {position}"
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def set_playback_position(ctx: Context, position: float) -> str:
    """Set playback position (in beats)"""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("set_playback_position", {"position": position})
        return f"Set playback position to {position}"
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def set_metronome(ctx: Context, enabled: bool) -> str:
    """Enable or disable metronome"""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("set_metronome", {"enabled": enabled})
        return f"Set metronome to {enabled}"
    except Exception as e:
        return f"Error: {str(e)}"

# ============================================================================
# ADVANCED TOOLS
# ============================================================================

@mcp.tool()
def quantize_notes(ctx: Context, track_index: int, clip_index: int, quantize_to: float = 0.25) -> str:
    """
    Quantize notes in a clip.

    Parameters:
    - track_index: Track index
    - clip_index: Clip slot index
    - quantize_to: Quantization grid in beats (0.25 = 16th note, 0.5 = 8th note, 1.0 = quarter note)
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("quantize_notes", {
            "track_index": track_index,
            "clip_index": clip_index,
            "quantize_to": quantize_to
        })
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def transpose_notes(ctx: Context, track_index: int, clip_index: int, semitones: int) -> str:
    """
    Transpose all notes in a clip.

    Parameters:
    - track_index: Track index
    - clip_index: Clip slot index
    - semitones: Number of semitones to transpose (positive or negative)
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("transpose_notes", {
            "track_index": track_index,
            "clip_index": clip_index,
            "semitones": semitones
        })
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def create_audio_track(ctx: Context, index: int = -1) -> str:
    """Create a new audio track at index (-1 = end)"""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("create_audio_track", {"index": index})
        return f"Created audio track '{result.get('name')}' at index {result.get('index')}"
    except Exception as e:
        return f"Error: {str(e)}"

# ============================================================================
# PLUGIN SUPPORT TOOLS
# ============================================================================

@mcp.tool()
def get_third_party_plugins(
    ctx: Context,
    creator: Optional[str] = None,
    plugin_type: Optional[str] = None,
    format: Optional[str] = None
) -> str:
    """
    Get 3rd party VST/AU/AAX plugins ONLY (excludes Ableton native devices).
    This is the recommended way to find plugins like FabFilter, Waves, Arturia, etc.

    Parameters:
    - creator: Filter by plugin creator/manufacturer (e.g., "FabFilter", "Waves", "Arturia")
               Uses Ableton's native manufacturer metadata from the plugin.
    - plugin_type: Filter by type ("instrument", "audio_effect", "midi_effect")
    - format: Filter by format ("VST2", "VST3", "AU", "AUv2", "AAX")

    Returns JSON with plugins array. Each plugin contains:
    {
      "name": "FabFilter Pro-Q 3",        // Full plugin name
      "uri": "query:Plugins#...",         // URI for loading (use with load_instrument_or_effect)
      "vendor": "FabFilter",              // Creator/manufacturer (from Ableton's native metadata)
      "format": "VST2",                   // Plugin format (detected from URI)
      "type": "audio_effect"              // Plugin type (detected from name)
    }

    Examples:
    - get_third_party_plugins(creator="FabFilter") → All FabFilter plugins
    - get_third_party_plugins(plugin_type="audio_effect") → All 3rd party effects
    - get_third_party_plugins(creator="FabFilter", plugin_type="audio_effect") → FabFilter effects only
    - get_third_party_plugins() → All 3rd party plugins

    Workflow:
    1. Call this tool with optional filters
    2. Find desired plugin in results (the 'vendor' field contains the manufacturer)
    3. Use load_instrument_or_effect(track_index, plugin['uri']) to load it
    """
    try:
        ableton = get_ableton_connection()
        # Send filters to Ableton for efficient filtering at the browser level
        result = ableton.send_command("get_third_party_plugins", {
            "creator": creator,
            "plugin_type": plugin_type,
            "format": format
        })

        if "plugins" not in result:
            return json.dumps(result, indent=2)

        filtered_result = {
            "plugins": result["plugins"],
            "count": result["count"],
            "filters_applied": {
                "creator": creator,
                "plugin_type": plugin_type,
                "format": format
            }
        }

        return json.dumps(filtered_result, indent=2)
    except Exception as e:
        logger.error(f"Error getting third party plugins: {str(e)}")
        return f"Error getting third party plugins: {str(e)}"

@mcp.tool()
def get_plugins_list(ctx: Context, plugin_type: str = "all") -> str:
    """
    Get list of available plugins from Ableton's browser (includes native + 3rd party).

    NOTE: For 3rd party plugins (VST/AU/AAX), use get_third_party_plugins() instead.
    This tool includes Ableton's native devices which can be confusing.

    Parameters:
    - plugin_type: Type of plugins ('all', 'instruments', 'audio_effects', 'midi_effects')

    Returns:
    - JSON with plugins array containing {name, uri, category}
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_plugins_list", {"plugin_type": plugin_type})
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting plugins list: {str(e)}")
        return f"Error getting plugins list: {str(e)}"

# Main execution
def main():
    """Run the MCP server"""
    mcp.run()

if __name__ == "__main__":
    main()