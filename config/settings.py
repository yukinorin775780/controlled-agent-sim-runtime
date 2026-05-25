"""
Configuration Settings Module
Centralized configuration constants for the Controlled Agent Sim Runtime project.
"""

import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- Secrets (Read from Environment) ---
# DO NOT hardcode the actual key here. Keep it in .env
API_KEY = os.getenv("BAILIAN_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
BASE_URL = os.getenv("DASHSCOPE_API_BASE")

# --- Model Configuration ---
MODEL_NAME = os.getenv("MODEL_NAME", "qwen-plus")  # Default to qwen-plus if not set
MAX_HISTORY = 10  # Threshold for RAG-Lite memory compression

# --- Game Mechanics ---
DEFAULT_DICE_DC = 10

# --- Paths ---
# Use relative paths from the project root
DATA_DIR = "characters"
SAVE_DIR = "data"  # Folder to store runtime saves (json)
TEMPLATE_DIR = "core/prompts"  # Folder for system templates
