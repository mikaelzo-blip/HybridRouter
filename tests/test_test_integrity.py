import pytest
from src.guard.test_integrity import TestIntegrityGuard, PatchValidationResult


@pytest.fixture
def guard():
    return TestIntegrityGuard()


def test_tier3_cannot_modify_test_files(guard):
    result = guard.validate_patch(
        tier="flash",
        file_path="tests/unit/test_auth.py",
        original_content="def test_auth(): assert True",
        new_content="def test_auth(): assert 1 == 1"
    )
    assert not result.approved
    assert result.reason == "TEST_FILE_READ_ONLY_FOR_TIER3"


def test_tier2_can_modify_test_files_if_valid(guard):
    result = guard.validate_patch(
        tier="pro",
        file_path="tests/unit/test_auth.py",
        original_content="def test_auth(): assert True",
        new_content="def test_auth():\n    assert True\n    assert 1 == 1"
    )
    assert result.approved


def test_reject_pytest_mark_skip(guard):
    # Pro attempts to add pytest.mark.skip -> rejected
    original = "def test_login():\n    assert True"
    new = "@pytest.mark.skip(reason='broken')\ndef test_login():\n    assert True"
    result = guard.validate_patch(
        tier="pro",
        file_path="tests/test_login.py",
        original_content=original,
        new_content=new
    )
    assert not result.approved
    assert result.reason == "TEST_TAMPERING_DETECTED"
    assert "pytest.mark.skip" in result.details


def test_reject_pytest_mark_xfail(guard):
    original = "def test_order():\n    assert True"
    new = "@pytest.mark.xfail\ndef test_order():\n    assert True"
    result = guard.validate_patch(
        tier="pro",
        file_path="tests/test_order.py",
        original_content=original,
        new_content=new
    )
    assert not result.approved
    assert result.reason == "TEST_TAMPERING_DETECTED"
    assert "pytest.mark.xfail" in result.details


def test_reject_javascript_test_skip_and_only(guard):
    original = "it('calculates total', () => { expect(1).toBe(1); });"
    new_skip = "it.skip('calculates total', () => { expect(1).toBe(1); });"
    new_only = "it.only('calculates total', () => { expect(1).toBe(1); });"
    
    res_skip = guard.validate_patch("pro", "test/calc.test.ts", original, new_skip)
    assert not res_skip.approved
    assert res_skip.reason == "TEST_TAMPERING_DETECTED"
    
    res_only = guard.validate_patch("pro", "test/calc.test.ts", original, new_only)
    assert not res_only.approved
    assert res_only.reason == "TEST_TAMPERING_DETECTED"


def test_reject_assertion_count_reduction_in_python(guard):
    original = """
def test_multiple_assertions():
    assert 1 == 1
    assert 2 == 2
    assert 3 == 3
"""
    new = """
def test_multiple_assertions():
    assert 1 == 1
"""
    result = guard.validate_patch(
        tier="pro",
        file_path="tests/test_math.py",
        original_content=original,
        new_content=new
    )
    assert not result.approved
    assert result.reason == "ASSERTION_COUNT_REDUCED"


def test_allow_legitimate_code_patch(guard):
    result = guard.validate_patch(
        tier="flash",
        file_path="src/services/payment.py",
        original_content="def pay(): return False",
        new_content="def pay(): return True"
    )
    assert result.approved
