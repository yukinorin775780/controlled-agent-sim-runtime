"""
d20 5th Edition Dice Rolling Module
Handles D20 System dice mechanics for controlled agent simulation simulation Agent
"""

import random
from enum import Enum
from typing import Dict, Any


class CheckResult(Enum):
    """Enumeration of possible check result types"""
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    CRITICAL_SUCCESS = "CRITICAL_SUCCESS"
    CRITICAL_FAILURE = "CRITICAL_FAILURE"


def roll_d20(dc: int, modifier: int = 0, roll_type: str = 'normal') -> Dict[str, Any]:
    """
    Simulates rolling a 20-sided die (D20) with d20 5e mechanics.
    
    Rules:
    - Normal: Roll 1d20
    - Advantage: Roll 2d20, take the HIGHER value
    - Disadvantage: Roll 2d20, take the LOWER value
    - Natural 20: Automatic success (CRITICAL SUCCESS), regardless of DC
    - Natural 1: Automatic failure (CRITICAL FAILURE)
    - Otherwise: Total = Raw Roll + Modifier. Success if Total >= DC
    
    Args:
        dc: Difficulty Class (target number to beat)
        modifier: Modifier to add to the roll (e.g., ability modifier, proficiency bonus)
        roll_type: Type of roll - 'normal', 'advantage', or 'disadvantage'
    
    Returns:
        Dictionary containing:
            - total: Final calculated score (raw_roll + modifier)
            - raw_roll: The dice roll result used (1-20)
            - rolls: List of all raw rolls (single value for normal, two values for adv/disadv)
            - is_success: Boolean indicating if the check succeeded
            - result_type: CheckResult enum value
            - log_str: Pre-formatted string for UI display
    """
    from core.engine.physics import DEBUG_ALWAYS_PASS_CHECKS

    # Normalize roll_type
    roll_type = roll_type.lower()
    if roll_type not in ['normal', 'advantage', 'disadvantage']:
        roll_type = 'normal'

    if DEBUG_ALWAYS_PASS_CHECKS:
        if roll_type == 'normal':
            rolls = [20]
        else:
            rolls = [20, 20]
        raw_roll = 20
        total = raw_roll + modifier
        result_type = CheckResult.CRITICAL_SUCCESS
        is_success = True
        dev_tag = " [DEV MODE] 自动大成功"
        if roll_type == 'normal':
            log_str = (
                f"🎲 ({raw_roll}) + {modifier:+d} = {total} vs DC {dc} [{result_type.value}]{dev_tag}"
            )
        elif roll_type == 'advantage':
            log_str = (
                f"🎲 [ADV] ({rolls[0]}, {rolls[1]}) -> {raw_roll} + {modifier:+d} = {total} vs DC {dc} "
                f"[{result_type.value}]{dev_tag}"
            )
        else:
            log_str = (
                f"🎲 [DIS] ({rolls[0]}, {rolls[1]}) -> {raw_roll} + {modifier:+d} = {total} vs DC {dc} "
                f"[{result_type.value}]{dev_tag}"
            )
        return {
            "total": total,
            "raw_roll": raw_roll,
            "rolls": rolls,
            "is_success": is_success,
            "result_type": result_type,
            "log_str": log_str,
        }

    # Roll the die(s)
    if roll_type == 'normal':
        rolls = [random.randint(1, 20)]
        raw_roll = rolls[0]
    elif roll_type == 'advantage':
        rolls = [random.randint(1, 20), random.randint(1, 20)]
        raw_roll = max(rolls)  # Take the higher value
    else:  # disadvantage
        rolls = [random.randint(1, 20), random.randint(1, 20)]
        raw_roll = min(rolls)  # Take the lower value
    
    # Calculate total (for display purposes, even if crit rules override)
    total = raw_roll + modifier
    
    # Determine result based on d20 5e rules
    if raw_roll == 20:
        # Natural 20: Critical Success (automatic success)
        result_type = CheckResult.CRITICAL_SUCCESS
        is_success = True
    elif raw_roll == 1:
        # Natural 1: Critical Failure (automatic failure)
        result_type = CheckResult.CRITICAL_FAILURE
        is_success = False
    else:
        # Normal roll: compare total to DC
        is_success = total >= dc
        if is_success:
            result_type = CheckResult.SUCCESS
        else:
            result_type = CheckResult.FAILURE
    
    # Format log string based on roll type
    if roll_type == 'normal':
        log_str = f"🎲 ({raw_roll}) + {modifier:+d} = {total} vs DC {dc} [{result_type.value}]"
    elif roll_type == 'advantage':
        log_str = f"🎲 [ADV] ({rolls[0]}, {rolls[1]}) -> {raw_roll} + {modifier:+d} = {total} vs DC {dc} [{result_type.value}]"
    else:  # disadvantage
        log_str = f"🎲 [DIS] ({rolls[0]}, {rolls[1]}) -> {raw_roll} + {modifier:+d} = {total} vs DC {dc} [{result_type.value}]"
    
    return {
        "total": total,
        "raw_roll": raw_roll,
        "rolls": rolls,
        "is_success": is_success,
        "result_type": result_type,
        "log_str": log_str
    }


def get_check_result_text(result_dict: Dict[str, Any]) -> str:
    """
    Generates a narrative description prompt based on the check result.
    This text can be injected into the LLM prompt to influence the narrative.
    
    Args:
        result_dict: Dictionary returned from roll_d20() function
    
    Returns:
        str: Narrative description of the check result
    """
    result_type = result_dict.get("result_type")
    
    if result_type == CheckResult.CRITICAL_SUCCESS:
        return "Check Result: CRITICAL SUCCESS! The action succeeds brilliantly."
    elif result_type == CheckResult.SUCCESS:
        return "Check Result: SUCCESS. The action succeeds."
    elif result_type == CheckResult.CRITICAL_FAILURE:
        return "Check Result: CRITICAL FAILURE! The action fails catastrophically."
    elif result_type == CheckResult.FAILURE:
        return "Check Result: FAILURE. The action fails."
    else:
        return "Check Result: Unknown result."
