"""
test_llm_utils.py - Unit Tests for LLM Utilities
=================================================

Tests JSON parsing, repair, and decision validation functions.
"""

import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from llm_utils import (
    strip_code_fences,
    extract_json_block,
    safe_json_loads,
    validate_decision
)


class TestStripCodeFences(unittest.TestCase):
    """Tests for strip_code_fences function."""
    
    def test_json_fence(self):
        """Should strip ```json fences."""
        text = '```json\n{"key": "value"}\n```'
        result = strip_code_fences(text)
        self.assertEqual(result.strip(), '{"key": "value"}')
    
    def test_plain_fence(self):
        """Should strip plain ``` fences."""
        text = '```\n{"key": "value"}\n```'
        result = strip_code_fences(text)
        self.assertEqual(result.strip(), '{"key": "value"}')
    
    def test_no_fence(self):
        """Should return text unchanged if no fences."""
        text = '{"key": "value"}'
        result = strip_code_fences(text)
        self.assertEqual(result, text)
    
    def test_empty_string(self):
        """Should handle empty string."""
        result = strip_code_fences("")
        self.assertEqual(result, "")


class TestExtractJsonBlock(unittest.TestCase):
    """Tests for extract_json_block function."""
    
    def test_simple_object(self):
        """Should extract simple JSON object."""
        text = 'Some text {"decision": "BUY"} more text'
        result = extract_json_block(text)
        self.assertEqual(result, '{"decision": "BUY"}')
    
    def test_nested_object(self):
        """Should extract nested JSON object."""
        text = 'prefix {"outer": {"inner": 123}} suffix'
        result = extract_json_block(text)
        self.assertIn('"outer"', result)
        self.assertIn('"inner"', result)
    
    def test_no_json(self):
        """Should return None if no JSON found."""
        text = 'Just plain text without JSON'
        result = extract_json_block(text)
        self.assertIsNone(result)
    
    def test_array(self):
        """Should extract JSON array."""
        text = 'prefix [1, 2, 3] suffix'
        result = extract_json_block(text)
        self.assertEqual(result, '[1, 2, 3]')


class TestSafeJsonLoads(unittest.TestCase):
    """Tests for safe_json_loads function."""
    
    def test_valid_json(self):
        """Should parse valid JSON."""
        text = '{"decision": "BUY", "confidence": 80}'
        obj, error = safe_json_loads(text)
        self.assertIsNotNone(obj)
        self.assertIsNone(error)
        self.assertEqual(obj["decision"], "BUY")
    
    def test_json_with_fences(self):
        """Should parse JSON with code fences."""
        text = '```json\n{"decision": "SELL"}\n```'
        obj, error = safe_json_loads(text)
        self.assertIsNotNone(obj)
        self.assertEqual(obj["decision"], "SELL")
    
    def test_json_with_text(self):
        """Should extract JSON from surrounding text."""
        text = 'Here is the decision: {"decision": "HOLD", "confidence": 50} That is all.'
        obj, error = safe_json_loads(text)
        self.assertIsNotNone(obj)
        self.assertEqual(obj["decision"], "HOLD")
    
    def test_trailing_comma_repair(self):
        """Should repair trailing commas."""
        text = '{"decision": "BUY", "confidence": 75,}'
        obj, error = safe_json_loads(text)
        self.assertIsNotNone(obj)
        self.assertEqual(obj["confidence"], 75)
    
    def test_empty_input(self):
        """Should return error for empty input."""
        obj, error = safe_json_loads("")
        self.assertIsNone(obj)
        self.assertEqual(error, "empty_input")
    
    def test_invalid_json(self):
        """Should return error for invalid JSON."""
        text = "This is not JSON at all"
        obj, error = safe_json_loads(text)
        self.assertIsNone(obj)
        self.assertIsNotNone(error)


class TestValidateDecision(unittest.TestCase):
    """Tests for validate_decision function."""
    
    def test_valid_buy_decision(self):
        """Should validate correct BUY decision."""
        obj = {
            "decision": "BUY",
            "confidence": 85,
            "sl_bias": "tighter",
            "tp_bias": "looser",
            "reason": "Strong trend"
        }
        result = validate_decision(obj)
        self.assertIsNotNone(result)
        self.assertEqual(result["decision"], "BUY")
        self.assertEqual(result["confidence"], 85)
        self.assertEqual(result["sl_bias"], "tighter")
    
    def test_valid_sell_decision(self):
        """Should validate correct SELL decision."""
        obj = {"decision": "SELL", "confidence": 70, "reason": "Reversal"}
        result = validate_decision(obj)
        self.assertIsNotNone(result)
        self.assertEqual(result["decision"], "SELL")
    
    def test_valid_hold_decision(self):
        """Should validate correct HOLD decision."""
        obj = {"decision": "HOLD", "confidence": 50}
        result = validate_decision(obj)
        self.assertIsNotNone(result)
        self.assertEqual(result["decision"], "HOLD")
    
    def test_invalid_decision_value(self):
        """Should reject invalid decision value."""
        obj = {"decision": "WAIT", "confidence": 50}
        result = validate_decision(obj)
        self.assertIsNone(result)
    
    def test_confidence_clamping(self):
        """Should clamp confidence to 0-100."""
        obj = {"decision": "BUY", "confidence": 150}
        result = validate_decision(obj)
        self.assertIsNotNone(result)
        self.assertEqual(result["confidence"], 100)
        
        obj2 = {"decision": "SELL", "confidence": -10}
        result2 = validate_decision(obj2)
        self.assertIsNotNone(result2)
        self.assertEqual(result2["confidence"], 0)
    
    def test_invalid_bias_normalized(self):
        """Should normalize invalid bias to neutral."""
        obj = {"decision": "BUY", "confidence": 80, "sl_bias": "invalid"}
        result = validate_decision(obj)
        self.assertIsNotNone(result)
        self.assertEqual(result["sl_bias"], "neutral")
    
    def test_reason_truncation(self):
        """Should truncate reason to 60 chars."""
        long_reason = "A" * 100
        obj = {"decision": "HOLD", "confidence": 50, "reason": long_reason}
        result = validate_decision(obj)
        self.assertIsNotNone(result)
        self.assertEqual(len(result["reason"]), 60)
    
    def test_default_values(self):
        """Should apply default values for optional fields."""
        obj = {"decision": "BUY", "confidence": 75}
        result = validate_decision(obj)
        self.assertIsNotNone(result)
        self.assertEqual(result["sl_bias"], "neutral")
        self.assertEqual(result["tp_bias"], "neutral")
        self.assertEqual(result["reason"], "")
    
    def test_non_dict_input(self):
        """Should reject non-dict input."""
        result = validate_decision("not a dict")
        self.assertIsNone(result)
        
        result2 = validate_decision(None)
        self.assertIsNone(result2)
        
        result3 = validate_decision([1, 2, 3])
        self.assertIsNone(result3)


if __name__ == "__main__":
    unittest.main()
