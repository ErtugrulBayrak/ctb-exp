"""
llm_utils.py - LLM Response Parsing & Validation Utilities
===========================================================

Robust utilities for parsing LLM JSON responses that may contain:
- Code fences (```json ... ```)
- Prose before/after JSON
- Partial/malformed JSON
- Schema violations

Also provides decision validation for trading signals.
"""

import json
import re
from typing import Any, Dict, Optional, Tuple


def strip_code_fences(text: str) -> str:
    """
    Remove markdown code fences from text.
    
    Handles:
    - ```json ... ```
    - ``` ... ```
    - Leading/trailing whitespace
    
    Args:
        text: Raw LLM response text
        
    Returns:
        Text with code fences removed
    """
    if not text:
        return ""
    
    text = text.strip()
    
    # Remove opening fence with optional language specifier
    text = re.sub(r'^```(?:json|JSON)?\s*\n?', '', text)
    
    # Remove closing fence
    text = re.sub(r'\n?```\s*$', '', text)
    
    return text.strip()


def extract_json_block(text: str) -> Optional[str]:
    """
    Extract JSON object or array using brace/bracket balancing.
    
    Handles:
    - Prose before/after JSON
    - Nested braces/brackets
    - Strings containing braces
    
    Args:
        text: Text potentially containing JSON
        
    Returns:
        Extracted JSON string or None if not found
    """
    if not text:
        return None
    
    text = strip_code_fences(text)
    
    # Find start of JSON (object or array)
    obj_start = text.find('{')
    arr_start = text.find('[')
    
    if obj_start == -1 and arr_start == -1:
        return None
    
    # Determine which comes first
    if arr_start == -1 or (obj_start != -1 and obj_start < arr_start):
        start_char = '{'
        end_char = '}'
        start_idx = obj_start
    else:
        start_char = '['
        end_char = ']'
        start_idx = arr_start
    
    # Brace-balanced extraction
    depth = 0
    in_string = False
    escape_next = False
    end_idx = -1
    
    for i in range(start_idx, len(text)):
        c = text[i]
        
        if escape_next:
            escape_next = False
            continue
        
        if c == '\\':
            escape_next = True
            continue
        
        if c == '"':
            in_string = not in_string
            continue
        
        if in_string:
            continue
        
        if c == start_char:
            depth += 1
        elif c == end_char:
            depth -= 1
            if depth == 0:
                end_idx = i
                break
    
    if end_idx == -1:
        # Unbalanced - try to find last occurrence of end_char
        last = text.rfind(end_char)
        if last > start_idx:
            end_idx = last
        else:
            return None
    
    return text[start_idx:end_idx + 1]


def safe_json_loads(text: str) -> Tuple[Optional[Any], Optional[str]]:
    """
    Safely parse JSON with repair attempts.
    
    Tries:
    1. Direct parse after fence stripping
    2. Extract JSON block and parse
    3. Minor repairs (trailing commas, etc.)
    
    Args:
        text: Raw text to parse
        
    Returns:
        Tuple of (parsed_object, error_message)
        - Success: (obj, None)
        - Failure: (None, "error description")
    """
    if not text:
        return None, "empty_input"
    
    # Step 1: Try direct parse after stripping fences
    cleaned = strip_code_fences(text)
    try:
        return json.loads(cleaned), None
    except json.JSONDecodeError:
        pass
    
    # Step 2: Extract JSON block and parse
    extracted = extract_json_block(text)
    if not extracted:
        return None, "no_json_block"
    
    try:
        return json.loads(extracted), None
    except json.JSONDecodeError:
        pass
    
    # Step 3: Minor repairs
    repaired = extracted
    
    # Remove trailing commas before } or ]
    repaired = re.sub(r',\s*}', '}', repaired)
    repaired = re.sub(r',\s*]', ']', repaired)
    
    # Fix single quotes to double quotes (risky but sometimes needed)
    # Only do this if no double quotes exist
    if '"' not in repaired and "'" in repaired:
        repaired = repaired.replace("'", '"')
    
    # Try parsing repaired version
    try:
        return json.loads(repaired), None
    except json.JSONDecodeError as e:
        return None, f"json_decode_error: {str(e)[:50]}"


def validate_decision(obj: Any) -> Optional[Dict[str, Any]]:
    """
    Validate and normalize a trading decision object.
    
    Required schema:
    - decision: "BUY" | "SELL" | "HOLD"
    - confidence: int 0-100
    - sl_bias: "tighter" | "looser" | "neutral" (optional, defaults to neutral)
    - tp_bias: "tighter" | "looser" | "neutral" (optional, defaults to neutral)
    - reason: str (max 60 chars)
    
    Args:
        obj: Parsed JSON object
        
    Returns:
        Validated dict or None if validation fails
    """
    if not isinstance(obj, dict):
        return None
    
    # Check required 'decision' field
    decision = obj.get("decision")
    if decision is None:
        # Try alternative key names
        decision = obj.get("action") or obj.get("signal")
    
    if not decision:
        return None
    
    decision = str(decision).upper().strip()
    if decision not in ("BUY", "SELL", "HOLD"):
        return None
    
    # Check 'confidence' field
    confidence = obj.get("confidence")
    if confidence is None:
        confidence = obj.get("conf") or obj.get("score")
    
    try:
        confidence = int(confidence)
    except (TypeError, ValueError):
        confidence = 0
    
    confidence = max(0, min(100, confidence))
    
    # Validate bias fields
    valid_biases = ("tighter", "looser", "neutral")
    
    sl_bias = str(obj.get("sl_bias", "neutral")).lower().strip()
    if sl_bias not in valid_biases:
        sl_bias = "neutral"
    
    tp_bias = str(obj.get("tp_bias", "neutral")).lower().strip()
    if tp_bias not in valid_biases:
        tp_bias = "neutral"
    
    # Validate reason
    reason = str(obj.get("reason", ""))[:60].strip()
    
    return {
        "decision": decision,
        "confidence": confidence,
        "sl_bias": sl_bias,
        "tp_bias": tp_bias,
        "reason": reason
    }


def build_retry_prompt(original_prompt: str) -> str:
    """
    Build a retry prompt that emphasizes JSON-only output.
    
    Args:
        original_prompt: The original prompt that failed
        
    Returns:
        A stricter prompt for retry
    """
    return f"""IMPORTANT: Your previous response was not valid JSON.

Please respond with ONLY a valid JSON object.
- Start with {{ and end with }}
- No markdown, no code fences, no prose
- Use double quotes for strings

Original request:
{original_prompt[:500]}

Output ONLY the JSON:"""


# ═══════════════════════════════════════════════════════════════════════════════
# TEST / DEMO
# ═══════════════════════════════════════════════════════════════════════════════

def demo():
    """LLM Utils demo - tüm parsing senaryolarını test eder."""
    print("\n" + "=" * 60)
    print("🧪 LLM UTILS DEMO")
    print("=" * 60 + "\n")
    
    test_cases = [
        # (Açıklama, Test Metni)
        ("✅ Temiz JSON", '{"decision": "BUY", "confidence": 85, "reason": "Strong momentum"}'),
        
        ("✅ Markdown fence ile", '''```json
{"decision": "SELL", "confidence": 70, "reason": "Resistance hit"}
```'''),
        
        ("✅ Prose ile çevrili", '''Here is my analysis based on the data:
{"decision": "HOLD", "confidence": 50, "reason": "Mixed signals"}
I hope this helps with your trading decisions.'''),
        
        ("✅ Trailing comma (tamir)", '{"decision": "BUY", "confidence": 90, "reason": "Breakout",}'),
        
        ("❌ JSON yok", 'I think you should buy because the market looks good.'),
        
        ("✅ Alternatif key (action)", '{"action": "BUY", "conf": 80, "reason": "Bull trend"}'),
    ]
    
    for i, (desc, text) in enumerate(test_cases, 1):
        print(f"Test {i}: {desc}")
        print(f"   Input: {text[:50]}..." if len(text) > 50 else f"   Input: {text}")
        
        obj, error = safe_json_loads(text)
        
        if obj:
            validated = validate_decision(obj)
            if validated:
                print(f"   ✅ Parsed: decision={validated['decision']}, confidence={validated['confidence']}")
            else:
                print(f"   ⚠️ Parsed JSON ama validation başarısız")
        else:
            print(f"   ❌ Parse hatası: {error}")
        
        print()
    
    print("-" * 60)
    print("📋 FONKSIYON TESTLERİ")
    print("-" * 60 + "\n")
    
    # strip_code_fences testi
    fenced = '```json\n{"test": true}\n```'
    stripped = strip_code_fences(fenced)
    print(f"strip_code_fences:")
    print(f"   Input:  {fenced}")
    print(f"   Output: {stripped}")
    print(f"   ✅ Pass" if stripped == '{"test": true}' else "   ❌ Fail")
    
    print()
    
    # extract_json_block testi
    messy = 'Blah blah {"key": "value"} more text'
    extracted = extract_json_block(messy)
    print(f"extract_json_block:")
    print(f"   Input:  {messy}")
    print(f"   Output: {extracted}")
    print(f"   ✅ Pass" if extracted == '{"key": "value"}' else "   ❌ Fail")
    
    print()
    
    # validate_decision testi
    raw_decision = {"action": "buy", "score": 75, "reason": "Test reason here"}
    validated = validate_decision(raw_decision)
    print(f"validate_decision:")
    print(f"   Input:  {raw_decision}")
    print(f"   Output: {validated}")
    expected = validated and validated["decision"] == "BUY" and validated["confidence"] == 75
    print(f"   ✅ Pass" if expected else "   ❌ Fail")
    
    print("\n" + "=" * 60)
    print("✅ Demo tamamlandı!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    demo()
