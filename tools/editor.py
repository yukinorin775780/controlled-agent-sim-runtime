# tools/editor.py
# Week 6 Day 4: Config Editor + Logic Visualizer
import streamlit as st
import yaml
import json
import os

# --- Constants ---
ITEMS_DB_PATH = "config/items.yaml"
CHAR_CONFIG_PATH = "characters/analyst.yaml"
MEMORY_PATH = "data/analyst_memory.json"

st.set_page_config(page_title="Controlled Agent Engine Dashboard", layout="wide", page_icon="⚔️")

# --- Helper Functions ---
def load_yaml(path):
    if not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}

def save_yaml(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False)

def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# --- Inventory Callbacks (run before rerun; modify disk directly) ---
def remove_item_callback(index: int):
    """Remove item at index from character YAML and save. Show toast."""
    conf = load_yaml(CHAR_CONFIG_PATH)
    inv = conf.get("inventory", [])
    if 0 <= index < len(inv):
        removed_id = inv.pop(index)
        conf["inventory"] = inv
        save_yaml(CHAR_CONFIG_PATH, conf)
        st.toast(f"Removed item at index {index}.")
    else:
        st.toast("Invalid index; no change.", icon="⚠️")


def add_item_callback(item_id: str):
    """Append item_id to character inventory in YAML and save. Show toast."""
    conf = load_yaml(CHAR_CONFIG_PATH)
    inv = conf.get("inventory", [])
    inv.append(item_id)
    conf["inventory"] = inv
    save_yaml(CHAR_CONFIG_PATH, conf)
    st.toast(f"Added: {item_id}", icon="✅")


# --- Data Loading ---
items_db = load_yaml(ITEMS_DB_PATH).get("items", {})
char_config = load_yaml(CHAR_CONFIG_PATH)
memory_data = load_json(MEMORY_PATH)

# --- Title ---
st.title("⚔️ Controlled Agent Narrative Engine - Admin Console")
st.markdown("---")

# --- Layout: Tabs ---
tab1, tab2 = st.tabs(["🛠️ Config & Assets", "🧠 Logic & State"])

# ==============================================================================
# TAB 1: Configuration (Day 3 Feature)
# ==============================================================================
with tab1:
    col1, col2 = st.columns(2)

    # [Left Column] Attributes
    with col1:
        st.subheader("👤 Character Attributes")
        
        # 1. Attributes
        attrs = char_config.get("attributes", {})
        new_attrs = {}
        for key, val in attrs.items():
            new_attrs[key] = st.slider(f"{key}", 1, 20, val)
        char_config["attributes"] = new_attrs

        # 2. Relationship
        st.divider()
        rel = char_config.get("relationship", 0)
        char_config["relationship"] = st.slider("💕 Relationship (Initial)", -100, 100, rel)

    # [Right Column] Inventory (callbacks write to disk; no in-memory remove/add + rerun)
    with col2:
        st.subheader("🎒 Inventory Management")
        
        current_inv = char_config.get("inventory", [])
        st.write(f"**Current Items ({len(current_inv)}):**")
        
        for i, item_id in enumerate(current_inv):
            c1, c2 = st.columns([3, 1])
            item_name = items_db.get(item_id, {}).get("name", item_id)
            c1.text(f"• {item_name} ({item_id})")
            c2.button(
                "❌",
                key=f"rm_{i}",
                on_click=remove_item_callback,
                args=(i,),
            )

        st.divider()
        
        st.write("**Add Item:**")
        item_options = {k: f"{k} - {v.get('name','Unknown')}" for k, v in items_db.items()}
        selected_key = st.selectbox(
            "Select Item DB",
            options=list(item_options.keys()),
            format_func=lambda x: item_options[x],
        )
        
        st.button(
            "➕ Add to Inventory",
            key="add_inv_btn",
            on_click=add_item_callback,
            args=(selected_key,),
        )

        # Keep sidebar save in sync: persist current in-memory inventory if user edited elsewhere
        char_config["inventory"] = current_inv

# ==============================================================================
# TAB 2: Logic & State (Day 4 Feature - NEW!)
# ==============================================================================
with tab2:
    if memory_data is None:
        st.warning("⚠️ No active game state found. Please run `main.py` first to generate a save file.")
    else:
        st.info(f"📂 Reading from Runtime Memory: `{MEMORY_PATH}`")
        
        col_logic_1, col_logic_2 = st.columns([1, 1])

        # --- Section A: Flag Monitor ---
        with col_logic_1:
            st.subheader("🚩 World Flags (Runtime)")
            flags = memory_data.get("flags", {})
            
            # Display Flags
            if not flags:
                st.caption("No flags set.")
            else:
                st.dataframe(flags, use_container_width=True)

            # Edit Flags
            with st.expander("🛠️ Add / Edit Flag"):
                with st.form("flag_form"):
                    new_flag_key = st.text_input("Flag Name (e.g., knows_secret)")
                    new_flag_val = st.checkbox("Set to True", value=True)
                    if st.form_submit_button("Set Flag"):
                        if new_flag_key:
                            memory_data["flags"][new_flag_key] = new_flag_val
                            save_json(MEMORY_PATH, memory_data)
                            st.success(f"Flag '{new_flag_key}' set to {new_flag_val}")
                            st.rerun()

        # --- Section B: Quest Inspector ---
        with col_logic_2:
            st.subheader("📜 Quest Tracker")
            quests_config = char_config.get("quests", [])
            current_flags = memory_data.get("flags", {})

            for q in quests_config:
                q_id = q.get("id", "unknown")
                trigger = q.get("trigger_event") # e.g. "flag:knows_secret"
                completer = q.get("completion_event")
                
                # Simple logic check
                is_active = False
                is_completed = False
                
                # Check Trigger
                if trigger and trigger.startswith("flag:"):
                    req_flag = trigger.split(":")[1]
                    if current_flags.get(req_flag, False):
                        is_active = True
                
                # Check Completion
                if completer and completer.startswith("flag:"):
                    end_flag = completer.split(":")[1]
                    if current_flags.get(end_flag, False):
                        is_completed = True

                # Determine Status UI
                if is_completed:
                    status_icon = "✅"
                    status_text = "COMPLETED"
                    color = "green"
                elif is_active:
                    status_icon = "🟢"
                    status_text = "ACTIVE"
                    color = "blue"
                else:
                    status_icon = "⚪"
                    status_text = "NOT STARTED"
                    color = "grey"

                # Render Card
                with st.expander(f"{status_icon} {q.get('title')} ({status_text})"):
                    st.write(f"**Description:** {q.get('description')}")
                    st.caption(f"Trigger: `{trigger}` | Completion: `{completer}`")
                    if is_active and not is_completed:
                        st.info("Task is currently in progress.")

# ==============================================================================
# SIDEBAR: Actions
# ==============================================================================
with st.sidebar:
    st.header("Actions")
    
    if st.button("💾 Save Config Changes", type="primary"):
        save_yaml(CHAR_CONFIG_PATH, char_config)
        st.success("Config saved to YAML!")
    
    st.markdown("---")
    st.header("⚠️ Danger Zone")
    if os.path.exists(MEMORY_PATH):
        if st.button("🗑️ Reset/Delete Save Data"):
            os.remove(MEMORY_PATH)
            st.warning("Save data deleted! Restarting...")
            st.rerun()
    else:
        st.caption("No save data detected.")