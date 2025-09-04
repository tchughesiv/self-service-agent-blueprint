"""Employee Info MCP Server.

A FastMCP server that provides tools for retrieving
employee laptop information.
"""

import logging
import os
from datetime import datetime

from employee_info.data import MOCK_EMPLOYEE_DATA
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse

MCP_TRANSPORT = os.environ.get("MCP_TRANSPORT", "sse")
MCP_PORT = int(
    os.environ.get("SELF_SERVICE_AGENT_EMPLOYEE_INFO_SERVICE_PORT_HTTP", "8000")
)
MCP_HOST = os.environ.get("MCP_HOST", "0.0.0.0")
mcp = FastMCP("Employee Info Server", host=MCP_HOST)


def _calculate_laptop_age(purchase_date_str: str) -> str:
    """Calculate the age of a laptop in years and months from purchase date.

    Args:
        purchase_date_str: Purchase date in YYYY-MM-DD format

    Returns:
        A string describing the laptop age in years and months
    """
    try:
        purchase_date = datetime.strptime(purchase_date_str, "%Y-%m-%d")
        current_date = datetime.now()

        # Calculate the difference
        years = current_date.year - purchase_date.year
        months = current_date.month - purchase_date.month

        # Adjust if the current day is before the purchase day in the month
        if current_date.day < purchase_date.day:
            months -= 1

        # Adjust years and months if months is negative
        if months < 0:
            years -= 1
            months += 12

        # Format the output
        if years == 0:
            return f"{months} month{'s' if months != 1 else ''}"
        elif months == 0:
            return f"{years} year{'s' if years != 1 else ''}"
        else:
            return f"{years} year{'s' if years != 1 else ''} and {months} month{'s' if months != 1 else ''}"

    except (ValueError, TypeError):
        return "Unable to calculate age (invalid date format)"


def _get_employee_laptop_info(employee_id: str) -> str:
    if not employee_id:
        raise ValueError("Employee ID cannot be empty")

    employee_data = MOCK_EMPLOYEE_DATA.get(employee_id)

    if not employee_data:
        available_ids = list(MOCK_EMPLOYEE_DATA.keys())
        raise ValueError(
            f"Employee ID '{employee_id}' not found. "
            f"Available IDs: {', '.join(available_ids)}"
        )

    # Calculate laptop age
    purchase_date = employee_data.get("laptop", {}).get("purchase_date")
    laptop_age = _calculate_laptop_age(purchase_date) if purchase_date else "Unknown"

    laptop_info = f"""
    Employee Name: {employee_data.get("name")}
    Employee ID: {employee_data.get("employee_id")}
    Employee Location: {employee_data.get("location")}
    Laptop Model: {employee_data.get("laptop_model")}
    Laptop Serial Number: {employee_data.get("laptop_serial_number")}
    Laptop Purchase Date: {purchase_date}
    Laptop Age: {laptop_age}
    Laptop Warranty Expiry Date: {employee_data.get("laptop", {}).get("warranty_expiry")}
    Laptop Warranty: {employee_data.get("laptop", {}).get("warranty_status")}
    """
    logging.info(
        f"returning laptop info for employee - employee_id: {employee_data.get('employee_id')}"
    )
    logging.info(f"{laptop_info}")
    return laptop_info


@mcp.custom_route("/health", methods=["GET"])
async def health(request):
    """Health check endpoint."""
    return JSONResponse({"status": "OK"})


@mcp.tool()
def get_employee_laptop_info(employee_id: str) -> str:
    """Return comprehensive laptop details for a given employee ID.

    This function retrieves and returns detailed information about an employee's laptop,
    including personal details, hardware specifications, purchase information, calculated
    age, and warranty status.

    Args:
        employee_id: The unique identifier for the employee (e.g., '1001')

    Returns:
        A formatted multi-line string containing the following information:
        - Employee Name: Full name of the employee
        - Employee ID: The provided employee identifier
        - Employee Location: Geographic region (EMEA, LATAM, APAC, etc.)
        - Laptop Model: Brand and model of the laptop
        - Laptop Serial Number: Unique serial number
        - Laptop Purchase Date: Date when laptop was purchased (YYYY-MM-DD format)
        - Laptop Age: Calculated age in years and months from purchase date to current date
        - Laptop Warranty Expiry Date: When the warranty expires (YYYY-MM-DD format)
        - Laptop Warranty: Current warranty status (Active/Expired)

    Raises:
        ValueError: If employee_id is empty or not found in the database

    Example:
        >>> get_employee_laptop_info("1001")
        "
        Employee Name: Alice Johnson
        Employee ID: 1001
        Employee Location: EMEA
        Laptop Model: Latitude 7420
        Laptop Serial Number: DL7420001
        Laptop Purchase Date: 2020-01-15
        Laptop Age: 4 years and 8 months
        Laptop Warranty Expiry Date: 2023-01-15
        Laptop Warranty: Expired
        "
    """
    return _get_employee_laptop_info(employee_id)


def main() -> None:
    """Run the Employee Info MCP server."""
    mcp.run(transport=MCP_TRANSPORT)


if __name__ == "__main__":
    main()
