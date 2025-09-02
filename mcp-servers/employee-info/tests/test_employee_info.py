"""Tests for the Employee Info MCP Server."""

import pytest
from employee_info.server import _get_employee_laptop_info
from mcp.server.fastmcp import Context


def test_get_employee_laptop_info_valid_employee():
    """Test retrieving laptop info for a valid employee."""
    # Create a mock context
    ctx = Context()
    result = _get_employee_laptop_info("1001", ctx)

    expected = """
    Employee Name: Alice Johnson
    Employee ID: 1001
    Employee Location: EMEA
    Laptop Model: Latitude 7420
    Laptop Serial Number: DL7420001
    Laptop Purchase Date: 2020-01-15
    Laptop Age: 5 years and 8 months
    Laptop Warranty Expiry Date: 2023-01-15
    Laptop Warranty: Expired
    """

    assert result == expected


def test_get_employee_laptop_info_invalid_employee():
    """Test error handling for invalid employee ID."""
    ctx = Context()
    with pytest.raises(ValueError) as exc_info:
        _get_employee_laptop_info("invalid_id", ctx)

    assert "not found" in str(exc_info.value)
    assert "Available IDs" in str(exc_info.value)


def test_get_employee_laptop_info_empty_employee_id():
    """Test error handling for empty employee ID."""
    ctx = Context()
    with pytest.raises(ValueError) as exc_info:
        _get_employee_laptop_info("", ctx)

    assert "cannot be empty" in str(exc_info.value)
