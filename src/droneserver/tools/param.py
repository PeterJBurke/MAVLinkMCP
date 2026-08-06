"""Parameter get/set MCP tools (MavSDK ``param`` plugin)."""

from mcp.server.fastmcp import Context

from droneserver.app import mcp
from droneserver.mavlink.connection import ensure_connection
from droneserver.telemetry.flight_log import LogColors, log_mavlink_cmd, logger


@mcp.tool()
async def get_parameter(ctx: Context, name: str, param_type: str = "auto") -> dict:
    """
    Get the value of a drone parameter by name.
    Parameters control drone behavior (e.g., flight speeds, sensor settings).
    Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.
        name (str): Parameter name (e.g., "RTL_ALT", "WPNAV_SPEED", "BATT_CAPACITY").
        param_type (str): Type of parameter - "int", "float", or "auto" (default: auto).
                          If "auto", will try float first, then int.

    Returns:
        dict: Parameter value and type, or error if parameter not found.

    Examples:
        - get_parameter("RTL_ALT", "float") - Get return-to-launch altitude
        - get_parameter("BATT_CAPACITY", "int") - Get battery capacity in mAh
        - get_parameter("WPNAV_SPEED") - Auto-detect parameter type
    """
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    logger.info(f"Getting parameter: {name} (type: {param_type})")

    try:
        if param_type == "int":
            value = await drone.param.get_param_int(name)
            return {"status": "success", "name": name, "value": value, "type": "int"}
        elif param_type == "float":
            value = await drone.param.get_param_float(name)
            return {"status": "success", "name": name, "value": value, "type": "float"}
        else:  # auto-detect
            # Try float first (most common)
            try:
                value = await drone.param.get_param_float(name)
                return {"status": "success", "name": name, "value": value, "type": "float"}
            except:
                # If float fails, try int
                value = await drone.param.get_param_int(name)
                return {"status": "success", "name": name, "value": value, "type": "int"}
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to get parameter {name}: {e}{LogColors.RESET}")
        return {
            "status": "failed",
            "error": f"Parameter '{name}' not found or inaccessible: {str(e)}",
            "suggestion": "Check parameter name spelling. Use list_parameters to see available parameters.",
        }


@mcp.tool()
async def set_parameter(ctx: Context, name: str, value: float, param_type: str = "auto") -> dict:
    """
    Set the value of a drone parameter by name.
    ⚠️ WARNING: Changing parameters can affect flight behavior. Only modify if you know what you're doing!
    Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.
        name (str): Parameter name (e.g., "RTL_ALT", "WPNAV_SPEED").
        value (float): New parameter value.
        param_type (str): Type of parameter - "int", "float", or "auto" (default: auto).
                          If "auto", will detect based on value (int if no decimal).

    Returns:
        dict: Confirmation of parameter change with old and new values.

    Examples:
        - set_parameter("RTL_ALT", 1500.0, "float") - Set RTL altitude to 15m
        - set_parameter("BATT_CAPACITY", 5200, "int") - Set battery capacity to 5200 mAh

    ⚠️ CAUTION:
        - Invalid parameters can make the drone unflyable
        - Always verify values are within safe ranges
        - Consider backing up parameters before changes
        - Some parameters require reboot to take effect
    """
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    logger.warning(f"⚠️ Setting parameter: {name} = {value} (type: {param_type})")

    try:
        # Get old value first
        try:
            if param_type == "int" or (param_type == "auto" and value == int(value)):
                old_value = await drone.param.get_param_int(name)
                param_type_final = "int"
            else:
                old_value = await drone.param.get_param_float(name)
                param_type_final = "float"
        except:
            old_value = None
            # Assume float if we can't get old value
            param_type_final = "float" if param_type == "auto" else param_type

        # Set new value
        if param_type_final == "int":
            log_mavlink_cmd("drone.param.set_param_int", name=name, value=int(value))
            await drone.param.set_param_int(name, int(value))
        else:
            log_mavlink_cmd("drone.param.set_param_float", name=name, value=float(value))
            await drone.param.set_param_float(name, float(value))

        logger.info(f"{LogColors.SUCCESS}✓ Parameter {name} changed from {old_value} to {value}{LogColors.RESET}")

        return {
            "status": "success",
            "name": name,
            "old_value": old_value,
            "new_value": int(value) if param_type_final == "int" else float(value),
            "type": param_type_final,
            "message": f"Parameter '{name}' set to {value}",
            "warning": "Some parameters may require a reboot to take effect.",
        }
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to set parameter {name}: {e}{LogColors.RESET}")
        return {
            "status": "failed",
            "error": f"Failed to set parameter '{name}': {str(e)}",
            "suggestion": "Verify parameter name and value are valid for this drone.",
        }


@mcp.tool()
async def list_parameters(ctx: Context, filter_prefix: str = "") -> dict:
    """
    List all available drone parameters.
    This can return a large number of parameters (100-1000+).
    Optionally filter by prefix to narrow results.
    Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.
        filter_prefix (str): Optional prefix to filter parameters (e.g., "BATT" for battery params).
                            Leave empty to get all parameters.

    Returns:
        dict: List of all parameters with their names, values, and types.

    Examples:
        - list_parameters() - Get ALL parameters (may be very long!)
        - list_parameters("RTL") - Get all Return-to-Launch parameters
        - list_parameters("BATT") - Get all battery-related parameters
        - list_parameters("WPNAV") - Get all waypoint navigation parameters

    Common Parameter Prefixes:
        - RTL_ : Return to Launch settings
        - BATT_ : Battery settings
        - WPNAV_ : Waypoint navigation
        - EK2_ / EK3_ : EKF (Extended Kalman Filter) settings
        - COMPASS_ : Compass/magnetometer settings
        - GPS_ : GPS settings
    """
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    logger.info(f"Listing parameters{f' (filter: {filter_prefix}*)' if filter_prefix else ''}")

    try:
        log_mavlink_cmd("drone.param.get_all_params", filter_prefix=filter_prefix if filter_prefix else "none")
        all_params = await drone.param.get_all_params()

        # Filter if prefix provided
        if filter_prefix:
            filter_upper = filter_prefix.upper()
            filtered = []
            for param in all_params.int_params:
                if param.name.upper().startswith(filter_upper):
                    filtered.append({"name": param.name, "value": param.value, "type": "int"})
            for param in all_params.float_params:
                if param.name.upper().startswith(filter_upper):
                    filtered.append({"name": param.name, "value": param.value, "type": "float"})

            filtered.sort(key=lambda x: x["name"])
            logger.info(f"Found {len(filtered)} parameters matching '{filter_prefix}*'")

            return {"status": "success", "filter": filter_prefix, "count": len(filtered), "parameters": filtered}
        else:
            # Return all parameters
            params_list = []
            for param in all_params.int_params:
                params_list.append({"name": param.name, "value": param.value, "type": "int"})
            for param in all_params.float_params:
                params_list.append({"name": param.name, "value": param.value, "type": "float"})

            params_list.sort(key=lambda x: x["name"])
            logger.info(f"Found {len(params_list)} total parameters")

            return {
                "status": "success",
                "count": len(params_list),
                "parameters": params_list,
                "warning": f"This is a large list ({len(params_list)} parameters). Consider using filter_prefix to narrow results.",
            }
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to list parameters: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Failed to retrieve parameters: {str(e)}"}
