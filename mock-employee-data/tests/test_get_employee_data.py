#!/usr/bin/env python3
"""Test script for the new get_employee_data() function with TEST_USERS support."""

import os
import sys

from mock_employee_data import MOCK_EMPLOYEE_DATA, get_employee_data

# Add the mock-employee-data package to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_without_test_users() -> None:
    """Test that get_employee_data() returns original data when TEST_USERS is not set."""
    # Ensure TEST_USERS is not set
    if "TEST_USERS" in os.environ:
        del os.environ["TEST_USERS"]

    result = get_employee_data()

    print("=== Test 1: Without TEST_USERS ===")
    print(f"Original data count: {len(MOCK_EMPLOYEE_DATA)}")
    print(f"Result data count: {len(result)}")
    print(f"Data matches: {result == MOCK_EMPLOYEE_DATA}")
    print()

    assert result == MOCK_EMPLOYEE_DATA


def test_with_test_users() -> None:
    """Test that get_employee_data() includes TEST_USERS when set."""
    # Set TEST_USERS environment variable
    test_emails = "tgolan@redhat.com,jdenver@redhat.com"
    os.environ["TEST_USERS"] = test_emails

    result = get_employee_data()

    print("=== Test 2: With TEST_USERS ===")
    print(f"TEST_USERS: {test_emails}")
    print(f"Original data count: {len(MOCK_EMPLOYEE_DATA)}")
    print(f"Result data count: {len(result)}")
    print(f"Expected count: {len(MOCK_EMPLOYEE_DATA) + 2}")

    # Check if test users were added
    test_users_found = []
    for email in ["tgolan@redhat.com", "jdenver@redhat.com"]:
        if email in result:
            test_users_found.append(email)
            user_data = result[email]
            print(f"\nTest user: {email}")
            print(f"  Name: {user_data['name']}")
            print(f"  Username: {user_data['user_name']}")
            print(f"  Sys ID: {user_data['sys_id']}")
            print(f"  Location: {user_data['location']}")
            print(f"  Laptop Model: {user_data['laptop_model']}")

    print(f"\nTest users found: {len(test_users_found)}")
    print(
        f"All original users preserved: {all(email in result for email in MOCK_EMPLOYEE_DATA.keys())}"
    )
    print()

    assert len(result) == len(MOCK_EMPLOYEE_DATA) + 2
    assert len(test_users_found) == 2
    assert all(email in result for email in MOCK_EMPLOYEE_DATA.keys())


def test_existing_user_not_duplicated() -> None:
    """Test that existing users in MOCK_EMPLOYEE_DATA are not duplicated."""
    # Use an existing email from MOCK_EMPLOYEE_DATA
    existing_email = "alice.johnson@company.com"
    test_emails = f"{existing_email},newuser@redhat.com"
    os.environ["TEST_USERS"] = test_emails

    result = get_employee_data()

    print("=== Test 3: Existing user not duplicated ===")
    print(f"TEST_USERS: {test_emails}")
    print(f"Original data count: {len(MOCK_EMPLOYEE_DATA)}")
    print(f"Result data count: {len(result)}")
    print(f"Expected count: {len(MOCK_EMPLOYEE_DATA) + 1}")  # Only new user added

    # Check that existing user wasn't duplicated
    alice_data = result.get(existing_email)
    original_alice_data = MOCK_EMPLOYEE_DATA.get(existing_email)

    print(f"\nExisting user preserved: {alice_data == original_alice_data}")
    print(f"New user added: {'newuser@redhat.com' in result}")
    print()

    assert len(result) == len(MOCK_EMPLOYEE_DATA) + 1
    assert alice_data == original_alice_data
    assert "newuser@redhat.com" in result


def main() -> int:
    """Run all tests."""
    print("Testing get_employee_data() function with TEST_USERS support")
    print("=" * 60)

    try:
        test_without_test_users()
        test_with_test_users()
        test_existing_user_not_duplicated()
    except AssertionError:
        print("❌ Some tests FAILED!")
        return 1

    print("=== Test Results ===")
    print("Test 1 (without TEST_USERS): PASS")
    print("Test 2 (with TEST_USERS): PASS")
    print("Test 3 (existing user not duplicated): PASS")
    print()
    print("🎉 All tests PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
