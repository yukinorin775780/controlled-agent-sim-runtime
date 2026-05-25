"""
Quest Management Module
Handles quest tracking and stage progression based on world state flags.
"""

from typing import List, Dict, Any, Optional
from core.systems import mechanics


class QuestManager:
    """
    Manages quest state and progression based on conditions.
    """
    
    @staticmethod
    def check_quests(quests_config: List[Dict[str, Any]], flags: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Check quest stages and return active/completed quests.
        
        Args:
            quests_config: List of quest configurations from YAML
            flags: Persistent world-state flags dictionary
        
        Returns:
            List of active/completed quest objects with current stage info
        """
        active_quests = []
        
        if not quests_config:
            return active_quests
        
        for quest in quests_config:
            quest_id = quest.get("id", "")
            quest_title = quest.get("title", "Unknown Quest")
            quest_description = quest.get("description", "")
            stages = quest.get("stages", [])
            
            if not stages:
                continue
            
            # Find the current stage (last stage where condition is True)
            current_stage = None
            current_stage_id = None
            is_completed = False
            stage_status = "ACTIVE"  # Default status
            
            for stage in stages:
                condition = stage.get("condition", "True")
                # Use mechanics.check_condition to evaluate
                if mechanics.check_condition(condition, flags):
                    current_stage = stage
                    current_stage_id = stage.get("id", "")
                    # Get the status from the stage (defaults to "ACTIVE")
                    stage_status = stage.get("status", "ACTIVE")
                    # Check if this stage is marked as completed
                    if stage_status == "COMPLETED":
                        is_completed = True
            
            # Only add quest if we found a matching stage
            if current_stage:
                active_quests.append({
                    "id": quest_id,
                    "title": quest_title,
                    "description": quest_description,
                    "stage_id": current_stage_id,
                    "stage_description": current_stage.get("description", ""),
                    "completed": is_completed,
                    "status": stage_status
                })
        
        return active_quests
